from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...types import Response


def _get_kwargs(
    bin_: str,
) -> Dict[str, Any]:
    _kwargs: Dict[str, Any] = {
        "method": "put",
        "url": f"/{bin_}",
    }

    return _kwargs


def _parse_response(*, client: Union[AuthenticatedClient, Client], response: httpx.Response) -> Optional[Any]:
    if response.status_code == HTTPStatus.OK:
        return None
    if response.status_code == HTTPStatus.NOT_FOUND:
        return None
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: Union[AuthenticatedClient, Client], response: httpx.Response) -> Response[Any]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    bin_: str,
    *,
    client: Union[AuthenticatedClient, Client],
) -> Response[Any]:
    """Lock an entire bin to make it read only

     This will make a bin read only. A read only bin does not accept new files to be uploaded or existing
    files to be updated. This provides some content integrity when distributing a bin to multiple
    parties. Note that it is possible to delete a read only bin.

    Args:
        bin_ (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any]
    """

    kwargs = _get_kwargs(
        bin_=bin_,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


async def asyncio_detailed(
    bin_: str,
    *,
    client: Union[AuthenticatedClient, Client],
) -> Response[Any]:
    """Lock an entire bin to make it read only

     This will make a bin read only. A read only bin does not accept new files to be uploaded or existing
    files to be updated. This provides some content integrity when distributing a bin to multiple
    parties. Note that it is possible to delete a read only bin.

    Args:
        bin_ (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any]
    """

    kwargs = _get_kwargs(
        bin_=bin_,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)
