if __name__ == "__main__":
    print("This is a subclass. Please use the main bot.py file.")
    exit()

from filebin_client import Client
from filebin_client.api.bin_ import get_bin, delete_bin, put_bin
from filebin_client.api.file import get_bin_filename, post_bin_filename
from filebin_client.types import File
import uuid
import json
import time

base_url = "https://filebin.net"

async def create_filebin(title: str = None):
    _client = Client(base_url=base_url, headers={'accept': 'application/json'})
    count = 0
    while True:
        MyBin = str(uuid.uuid4())

        if await is_bin_empty(MyBin):
            break
        else:
            print(f'Bin {MyBin} already exists. Trying again in .15 seconds...')
            time.sleep(0.15)
            if count >= 5:
                print(f'Timeout. Tried creating new bin 5 times. Exiting...')
                return None
            count += 1

    print(f'Created bin: {base_url}/{MyBin}')

    payload = "Upload Label or nglyph file".encode('utf-8')
    if title is not None:
        payload += " for {title}".encode('utf-8')

    body = File(
        payload=payload,
    )

    result = post_bin_filename.sync_detailed(
        bin_=MyBin,
        filename=payload,
        client=_client,
        body=body,
    )

    return f'{MyBin}'


async def delete_filebin(bin):
    _client = Client(base_url=base_url, headers={'accept': 'application/json'})

    result = delete_bin.sync_detailed(
        bin_=bin,
        client=_client
    )

    print(f'Deleted bin: {bin}')


async def get_files_in_bin(bin):
    _client = Client(base_url=base_url, headers={'accept': 'application/json'})

    result = get_bin.sync_detailed(
        bin_=bin,
        client=_client
    )

    result = result.content.decode()
    result = json.loads(result)
    files = result['files']


    filenames = []
    for file in files:
        filenames.append(file['filename'])

    return filenames


async def check_for_glyph_or_txt_files_in_bin(bin):
    files = await get_files_in_bin(bin)
    if len(files) == 0:
        return False
    for file in files:
        if file.endswith('.nglyph'):
            return True
        elif file.endswith('.txt'):
            return True
    return False

async def check_for_nglyph_file_in_bin(bin):
    files = await get_files_in_bin(bin)
    if len(files) == 0:
        return False
    for file in files:
        if file.endswith('.nglyph'):
            return True
    return False 

async def is_bin_empty(bin):
    _client = Client(base_url=base_url, headers={'accept': 'application/json'})

    result = get_bin.sync_detailed(
        bin_=bin,
        client=_client
    )
    
    result = result.content.decode()
    result = json.loads(result)
    number_of_files = result['bin']['files']

    print(f'Number of files in bin: {number_of_files}')

    if number_of_files == 0:
        print(f'Bin {bin} is empty.')
        return True
    else:
        print(f'Bin {bin} is not empty.')
        return False


async def lock_filebin(bin):
    _client = Client(base_url=base_url, headers={'accept': 'application/json'})

    result = put_bin.sync_detailed(
        bin_=bin,
        client=_client
    )

    print(f'Locked bin: {bin}')

async def download_file_from_bin(bin, filename):
    _client = Client(base_url=base_url, headers={'accept': 'application/json'})

    result = get_bin_filename.sync_detailed(
        bin_=bin,
        filename=filename,
        client=_client
    )
    location = result.headers['location']
    print(location)
    
    # get request at location and write to file
    async with _client.get_async_httpx_client() as client:
        async with client.stream('GET', location) as response:
            with open(filename, 'wb') as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)

    # return filename and path
    return filename