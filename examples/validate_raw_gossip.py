#!/usr/bin/env python3
"""
validate_raw_gossip.py

Validates that the raw_gossip BYTEA column in the three tables follows the pattern:

    <varint(payload_len)> <2-byte msg_type> <payload of payload_len bytes>

where the varint encodes only the payload length – it does NOT include the
2-byte message type prefix.

Usage:
    DATABASE_URL=postgresql://user:pass@host/db python validate_raw_gossip.py

Environment variables:
    DATABASE_URL   full libpq connection string (required)
    BIG_ENDIAN     set to "1" to decode varint as big-endian (default: little-endian)
    WORKERS        parallel threads per table (default: 8)
"""

import io
import os
import struct
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extensions
from dotenv import load_dotenv

from lnhistoryclient.constants import GOSSIP_TYPE_NAMES
from lnhistoryclient.parser.common import varint_decode

load_dotenv(".env")

# ── Config ────────────────────────────────────────────────────────────────────

POSTGRES_USER = os.environ.get("POSTGRES_USER")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD")
POSTGRES_HOST = os.environ.get("POSTGRES_HOST")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT")
POSTGRES_DBNAME = os.environ.get("POSTGRES_DBNAME")
DB_CONNECTION = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}" f"@{POSTGRES_HOST}:{POSTGRES_PORT}" f"/{POSTGRES_DBNAME}"
)

BIG_ENDIAN = os.environ.get("BIG_ENDIAN", "0") == "1"
WORKERS = int(os.environ.get("WORKERS", "8"))

TABLES = ["channel_updates"]
MAX_VERBOSE_ERRORS = 10
MAX_PAGE = None  # sentinel: last chunk has no upper bound (avoids tid overflow)
PROGRESS_INTERVAL = 2.0  # seconds between progress log lines
FETCH_SIZE = 5_000  # rows fetched per server round-trip

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class InvalidRow:
    row_num: int
    reason: str
    msg_type: int
    raw_hex_prefix: str


@dataclass
class ChunkResult:
    total: int = 0
    valid: int = 0
    type_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    unknown_types: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    error_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    invalid_samples: List[InvalidRow] = field(default_factory=list)


# ── Validation (pure, no I/O) ─────────────────────────────────────────────────


def validate_row(raw: bytes, big_endian: bool) -> Tuple[bool, str, int]:
    """
    Check one raw_gossip BYTEA against the Lightning Network wire / TLV format:

        [varint(total_msg_len)] [2-byte msg_type] [payload of (total_msg_len - 2) bytes]

    The varint encodes the TOTAL message length INCLUDING the 2-byte type prefix.
    This matches how parse_gossip_messages() reads gossip files and how
    read_pg_copy_single_column_binary() now parses BYTEA columns.

    Returns (ok, error_reason, msg_type).  msg_type is -1 when unreadable.
    """
    stream = io.BytesIO(raw)

    # Step 1: varint = total length of (type + payload)
    total_msg_len = varint_decode(stream, big_endian=big_endian)
    if total_msg_len is None:
        return False, "varint decode failed", -1
    if total_msg_len < 2:
        return False, f"total_msg_len too small: {total_msg_len}", -1

    varint_size = stream.tell()

    # Step 2: read exactly total_msg_len bytes = [type(2)] + [payload]
    inner = stream.read(total_msg_len)
    if len(inner) != total_msg_len:
        return False, f"truncated: expected {total_msg_len} inner bytes, got {len(inner)}", -1

    # Step 3: nothing should remain
    leftover = stream.read()
    if leftover:
        return False, f"{len(leftover)} leftover bytes after message", -1

    # Step 4: cross-check total BYTEA length
    if len(raw) != varint_size + total_msg_len:
        return False, f"length mismatch: raw={len(raw)} expected={varint_size + total_msg_len}", -1

    msg_type = struct.unpack(">H", inner[:2])[0]
    return True, "", msg_type


# ── Worker ────────────────────────────────────────────────────────────────────


def process_chunk(
    table: str,
    page_lo: int,
    page_hi: Optional[int],
    big_endian: bool,
    counter: "SharedCounter",
) -> ChunkResult:
    """
    Open a private connection, scan [page_lo, page_hi) of table via ctid range,
    validate each row, and return aggregated results.
    Each page range is a disjoint slice of the table's physical pages, so workers
    never overlap and no locking on the DB side is needed.
    page_hi=None means "scan to end of table" (used for the last chunk to avoid
    overflowing PostgreSQL's tid uint32 page number).
    """
    result = ChunkResult()
    conn = psycopg2.connect(DB_CONNECTION)
    conn.set_session(readonly=True)
    try:
        with conn.cursor(name=f"chunk_{table}_{page_lo}") as cur:
            cur.itersize = FETCH_SIZE
            if page_hi is None:
                cur.execute(
                    f"SELECT raw_gossip FROM {table} WHERE ctid >= %s::tid",  # noqa: S608
                    (f"({page_lo},0)",),
                )
            else:
                cur.execute(
                    f"SELECT raw_gossip FROM {table} "  # noqa: S608
                    f"WHERE ctid >= %s::tid AND ctid < %s::tid",
                    (f"({page_lo},0)", f"({page_hi},0)"),
                )
            for (raw_gossip,) in cur:
                raw = bytes(raw_gossip)
                result.total += 1

                ok, reason, msg_type = validate_row(raw, big_endian)
                if ok:
                    result.valid += 1
                    name = GOSSIP_TYPE_NAMES.get(msg_type, f"0x{msg_type:04x}")
                    result.type_counts[name] += 1
                else:
                    result.error_counts[reason] += 1
                    if len(result.invalid_samples) < MAX_VERBOSE_ERRORS:
                        result.invalid_samples.append(InvalidRow(result.total, reason, msg_type, raw[:24].hex()))

                # Report every 1 000 rows so the progress thread stays current
                if result.total % 1_000 == 0:
                    counter.add(1_000)

        # Report the remainder
        counter.add(result.total % 1_000)
    finally:
        conn.close()

    return result


# ── Shared counter + progress logger ─────────────────────────────────────────


class SharedCounter:
    """Lock-protected integer counter shared across worker threads."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def add(self, n: int) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


class ProgressLogger:
    """Daemon thread that prints rows/s every PROGRESS_INTERVAL seconds."""

    def __init__(self, table: str, row_estimate: int, counter: SharedCounter) -> None:
        self._table = table
        self._estimate = row_estimate
        self._counter = counter
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        last_time = self._t0
        last_count = 0

        while not self._stop.wait(PROGRESS_INTERVAL):
            now = time.monotonic()
            processed = self._counter.value
            elapsed = now - self._t0
            interval = now - last_time

            instant_rate = (processed - last_count) / interval if interval > 0 else 0.0
            overall_rate = processed / elapsed if elapsed > 0 else 0.0
            pct = f"{processed / self._estimate * 100:5.1f}%" if self._estimate else "   ?%"

            print(
                f"  [{self._table}]  {processed:>10,} / ~{self._estimate:>10,}  {pct}"
                f"  {instant_rate:>8,.0f} rows/s  (avg {overall_rate:>8,.0f} rows/s)",
                flush=True,
            )
            last_time = now
            last_count = processed

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()


# ── Table helpers ─────────────────────────────────────────────────────────────


def _table_meta(conn: psycopg2.extensions.connection, table: str) -> Tuple[int, int]:
    """Return (relpages, reltuples) estimates from pg_class."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT relpages, reltuples::bigint FROM pg_class WHERE relname = %s",
            (table,),
        )
        row = cur.fetchone()
        return (int(row[0]), int(row[1])) if row else (1, 0)


def _split_pages(relpages: int, n_workers: int) -> List[Tuple[int, Optional[int]]]:
    """Divide [0, relpages) into n_workers disjoint ctid page ranges.
    The last range always uses None as the upper bound so the worker scans
    to the physical end of the table without overflowing PostgreSQL's tid type.
    """
    if relpages == 0:
        return [(0, None)]
    chunk = max(1, (relpages + n_workers - 1) // n_workers)
    ranges: List[Tuple[int, Optional[int]]] = []
    lo = 0
    while lo < relpages:
        hi = lo + chunk
        ranges.append((lo, hi))
        lo = hi
    # Last chunk: no upper bound — scans to end of table
    ranges[-1] = (ranges[-1][0], None)
    return ranges


# ── Per-table orchestration ───────────────────────────────────────────────────


def check_table(
    meta_conn: psycopg2.extensions.connection,
    table: str,
    big_endian: bool,
    n_workers: int,
) -> None:
    relpages, reltuples = _table_meta(meta_conn, table)
    ranges = _split_pages(relpages, n_workers)

    bar = "─" * 70
    print(f"\n{bar}")
    print(
        f"Table: {table}   ~{reltuples:,} rows   {relpages:,} pages"
        f"  →  {len(ranges)} chunks  ×  {n_workers} workers"
    )
    print(bar)

    counter = SharedCounter()
    progress = ProgressLogger(table, reltuples, counter)
    t_start = time.monotonic()

    # Aggregate across all chunk results
    total = 0
    valid = 0
    type_counts: Dict[str, int] = defaultdict(int)
    unknown_types: Dict[int, int] = defaultdict(int)
    error_counts: Dict[str, int] = defaultdict(int)
    invalid_samples: List[InvalidRow] = []

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(process_chunk, table, lo, hi, big_endian, counter): (lo, hi) for lo, hi in ranges}
        for future in as_completed(futures):
            res: ChunkResult = future.result()
            total += res.total
            valid += res.valid
            for k, v in res.type_counts.items():
                type_counts[k] += v
            for k, v in res.unknown_types.items():
                unknown_types[k] += v
            for k, v in res.error_counts.items():
                error_counts[k] += v
            if len(invalid_samples) < MAX_VERBOSE_ERRORS:
                invalid_samples.extend(res.invalid_samples)

    progress.stop()

    elapsed = time.monotonic() - t_start
    invalid = total - valid
    overall_rate = total / elapsed if elapsed > 0 else 0

    # ── Final report ────────────────────────────────────────────────────────
    print(f"\n  Finished in {elapsed:.1f}s  ({overall_rate:,.0f} rows/s overall)")
    print(f"  Total rows   : {total:>10,}")
    print(f"  Valid        : {valid:>10,}  ({valid / total * 100:.2f}%)" if total else "  Valid        : 0")
    print(f"  Invalid      : {invalid:>10,}")

    if type_counts:
        print("\n  Message type distribution (valid rows):")
        for name, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"    {name:<35} {count:>12,}")

    if unknown_types:
        print("\n  Unknown message types:")
        for t, count in sorted(unknown_types.items()):
            print(f"    0x{t:04x} ({t:<6})  {count:>12,}")

    if error_counts:
        print("\n  Error breakdown:")
        for reason, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            print(f"    {reason:<52} {count:>8,}")

    if invalid_samples:
        shown = invalid_samples[:MAX_VERBOSE_ERRORS]
        print(f"\n  First {len(shown)} invalid rows:")
        for s in shown:
            type_str = GOSSIP_TYPE_NAMES.get(s.msg_type, f"0x{s.msg_type:04x}") if s.msg_type >= 0 else "n/a"
            print(f"    row {s.row_num:>8,}  type={type_str:<22}  reason={s.reason}")
            print(f"             hex prefix: {s.raw_hex_prefix}…")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    endian_label = "big-endian" if BIG_ENDIAN else "little-endian (Bitcoin standard)"
    print(f"Varint encoding : {endian_label}")
    print(f"Workers / table : {WORKERS}")
    print(f"Tables          : {', '.join(TABLES)}")

    meta_conn = psycopg2.connect(DB_CONNECTION)
    meta_conn.set_session(readonly=True)

    try:
        for table in TABLES:
            check_table(meta_conn, table, big_endian=BIG_ENDIAN, n_workers=WORKERS)
    finally:
        meta_conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
