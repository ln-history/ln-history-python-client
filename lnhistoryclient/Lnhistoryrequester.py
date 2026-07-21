"""Backward-compatibility shim.

The requester moved to :mod:`lnhistoryclient.api.requester` when it was rewritten for
the current API. Import from there in new code:

    from lnhistoryclient.api import LnhistoryRequester
"""

from lnhistoryclient.api.requester import LnhistoryRequester, LnhistoryRequesterError

__all__ = ["LnhistoryRequester", "LnhistoryRequesterError"]
