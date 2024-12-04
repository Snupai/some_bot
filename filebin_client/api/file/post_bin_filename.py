from http import HTTPStatus
from typing import Any, Dict, Optional, Union

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...types import UNSET, Response, Unset


def _get_kwargs(
    bin_: str,
    filename: str,
    *,
    body: str,
    cid: Union[Unset, str] = UNSET,
) -> Dict[str, Any]:
    headers: Dict[str, Any] = {}
    if not isinstance(cid, Unset):
        headers["cid"] = cid

    _kwargs: Dict[str, Any] = {
        "method": "post",
        "url": f"/{bin_}/{filename}",
    }

    _body = body.payload

    _kwargs["content"] = _body
    headers["Content-Type"] = "application/octet-stream"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: Union[AuthenticatedClient, Client], response: httpx.Response) -> Optional[Any]:
    if response.status_code == HTTPStatus.CREATED:
        return None
    if response.status_code == HTTPStatus.BAD_REQUEST:
        return None
    if response.status_code == HTTPStatus.FORBIDDEN:
        return None
    if response.status_code == HTTPStatus.METHOD_NOT_ALLOWED:
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
    filename: str,
    *,
    client: Union[AuthenticatedClient, Client],
    body: str,
    cid: Union[Unset, str] = UNSET,
) -> Response[Any]:
    """Upload a file to a bin

     Upload a file to a new or existing bin. The bin will be created if it does not exist prior to the
    upload.

    Args:
        bin_ (str):
        filename (str):
        cid (Union[Unset, str]):
        body (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any]
    """

    kwargs = _get_kwargs(
        bin_=bin_,
        filename=filename,
        body=body,
        cid=cid,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


async def asyncio_detailed(
    bin_: str,
    filename: str,
    *,
    client: Union[AuthenticatedClient, Client],
    body: str,
    cid: Union[Unset, str] = UNSET,
) -> Response[Any]:
    """Upload a file to a bin

     Upload a file to a new or existing bin. The bin will be created if it does not exist prior to the
    upload.

    Args:
        bin_ (str):
        filename (str):
        cid (Union[Unset, str]):
        body (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any]
    """

    kwargs = _get_kwargs(
        bin_=bin_,
        filename=filename,
        body=body,
        cid=cid,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)
