import os
import requests
import datetime
import shutil
import json
import sys
from typing import List, Optional

# --- IMPORTS NEEDED FOR THE SSL FIX ---
import ssl
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.poolmanager import PoolManager

# --- Custom Adapter to handle SSL/TLS incompatibility ---
class TlsAdapter(HTTPAdapter):
    """A custom requests adapter to handle servers with older SSL/TLS protocols."""
    def init_poolmanager(self, connections, maxsize, block=False):
        """Create a pool manager with a custom, more compatible SSL context."""
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1') # Use a more compatible security level
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx
        )

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DRIME_API_URL = "https://app.drime.cloud/api/v1"
DRIME_API_KEY = "3449|CvNDnkwSz1nVbpfx1WLqAmTVg7N0haSwZb6R8xjgdbfed638"

SOURCES_TO_BACKUP: List[str] = [
    'users.txt',
    'user_notes',
    'user_uploads'
]

# --- Helper Functions ---

def create_backup_archive() -> Optional[str]:
    """
    Creates a timestamped ZIP archive of specified sources and returns its full path.

    A temporary directory is used to assemble the files before zipping, which
    is cleaned up automatically.

    Returns:
        Optional[str]: The full path to the created .zip archive, or None if creation fails.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_name = f'quickpad_backup_{timestamp}'
    archive_path_without_ext = os.path.join(BASE_DIR, archive_name)
    temp_archive_dir = os.path.join(BASE_DIR, f"temp_backup_{timestamp}")

    print("Starting backup process...")
    print(f"Creating archive: {archive_name}.zip")
    os.makedirs(temp_archive_dir, exist_ok=True)

    try:
        for src_name in SOURCES_TO_BACKUP:
            source_path = os.path.join(BASE_DIR, src_name)
            if os.path.exists(source_path):
                dest_path = os.path.join(temp_archive_dir, src_name)
                if os.path.isdir(source_path):
                    shutil.copytree(source_path, dest_path)
                else:
                    shutil.copy2(source_path, dest_path)
                print(f"  - Added '{src_name}' to be archived.")
            else:
                print(f"  - WARNING: Source '{src_name}' not found, skipping.")

        final_archive_path = shutil.make_archive(
            base_name=archive_path_without_ext,
            format='zip',
            root_dir=temp_archive_dir
        )
        print("Archive created successfully.")
        return final_archive_path
    except Exception as e:
        print(f"ERROR: Failed to create archive: {e}")
        return None
    finally:
        if os.path.exists(temp_archive_dir):
            shutil.rmtree(temp_archive_dir)
        
def upload_to_drime_root(token: str, file_path: str) -> Optional[int]:
    """
    Uploads a file to the Drime.Cloud root directory.

    Args:
        token (str): The API authorization bearer token.
        file_path (str): The local path to the file to upload.

    Returns:
        Optional[int]: The HTTP status code of the upload request, or None on a connection error.
    """
    filename = os.path.basename(file_path)
    print(f"Uploading '{filename}' to Drime.Cloud root directory...")
    
    main_url = f"{DRIME_API_URL}/uploads"
    headers = {"Authorization": f"Bearer {token}"}

    session = requests.Session()
    session.mount('https://', TlsAdapter())

    try:
        with open(file_path, 'rb') as f:
            files_payload = {'file': (filename, f, 'application/zip')}
            response = session.post(main_url, headers=headers, files=files_payload, timeout=600)
            response.raise_for_status() # Raises an HTTPError for bad responses (4xx or 5xx)

        print(f"Upload successful with status code: {response.status_code}")
        return response.status_code
    except requests.exceptions.HTTPError as http_err:
        print(f"ERROR: HTTP error during upload: {http_err}")
        print(f"Server response: {http_err.response.text}")
        return http_err.response.status_code
    except requests.exceptions.RequestException as e:
        print(f"ERROR: A network error occurred during upload: {e}")
        return None

def main() -> None:
    """Main function to run the entire backup process."""
    print(f"\n--- Starting QuickPad Backup on {datetime.datetime.now()} ---")
    
    if not DRIME_API_KEY:
        print("FATAL ERROR: DRIME_API_KEY is not set. Exiting.");
        sys.exit(1)

    archive_path = None
    try:
        # Step 1: Create the local backup archive
        archive_path = create_backup_archive()
        if not archive_path:
            print("ERROR: Backup archive creation failed. Aborting process.")
            sys.exit(1)

        # Step 2: Upload the archive to the cloud
        upload_status = upload_to_drime_root(DRIME_API_KEY, archive_path)
        # A successful upload returns a status code (e.g., 200 or 201), which is a "truthy" value.
        # A failed upload returns None or a 4xx/5xx code. We only check for None here,
        # as raise_for_status() handles HTTP errors.
        if upload_status is None:
            print("ERROR: Drime.Cloud upload failed due to a connection or request error.")
            sys.exit(1)

    except Exception as e:
        # Catch any unexpected exceptions during the process
        print(f"\n--- An unexpected error occurred during the backup process: {e} ---")
    finally:
        # Step 3: Clean up the local archive file
        if archive_path and os.path.exists(archive_path):
            print(f"Cleaning up local archive: {os.path.basename(archive_path)}")
            os.remove(archive_path)

    print(f"--- Backup process finished at {datetime.datetime.now()} ---")

if __name__ == "__main__":
    main()
