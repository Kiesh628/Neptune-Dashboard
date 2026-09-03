import os
import json
import io
import re
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from control import GOOGLE_FOLDER_ID, GOOGLE_TOKEN_JSON, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, STUDY_NAME

def get_drive_service():
    """Build and return the Google Drive service instance."""
    if not all([GOOGLE_FOLDER_ID, GOOGLE_TOKEN_JSON, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET]):
        raise ValueError("Missing Google Drive credentials in control.py or environment variables.")
        
    client_config = {
        "installed": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    
    token_dict = json.loads(GOOGLE_TOKEN_JSON) # type: ignore
    creds = Credentials(
        token=token_dict.get('token'),
        refresh_token=token_dict.get('refresh_token'),
        token_uri=token_dict.get('token_uri'),
        client_id=client_config['installed']['client_id'],
        client_secret=client_config['installed']['client_secret'],
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

_cached_study_folder_id = None

def _get_or_create_study_folder(service):
    """Find or create a subfolder named STUDY_NAME under GOOGLE_FOLDER_ID."""
    global _cached_study_folder_id
    if _cached_study_folder_id is not None:
        return _cached_study_folder_id
        
    query = f"'{GOOGLE_FOLDER_ID}' in parents and name = '{STUDY_NAME}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    folders = results.get('files', [])
    
    if folders:
        _cached_study_folder_id = folders[0]['id']
    else:
        file_metadata = {
            'name': STUDY_NAME,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [GOOGLE_FOLDER_ID]
        }
        folder = service.files().create(body=file_metadata, fields='id').execute()
        _cached_study_folder_id = folder.get('id')
        
    return _cached_study_folder_id

def upload_file(filename: str, mimetype: str = "application/octet-stream", delete_local: bool = True) -> None:
    """Upload a file to Google Drive under the designated folder, optionally deleting the local copy."""
    if not os.path.exists(filename):
        return
        
    basename = os.path.basename(filename)
    max_retries = 3
    success = False
    
    for attempt in range(1, max_retries + 1):
        try:
            service = get_drive_service()
            study_folder_id = _get_or_create_study_folder(service)
            file_metadata = {'name': basename, 'parents': [study_folder_id]}
            
            with open(filename, 'rb') as f:
                # Use resumable=True to handle large files and network interrupts robustly
                media = MediaIoBaseUpload(f, mimetype=mimetype, resumable=True)
                service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                
            print(f"[GDrive] Uploaded {basename} successfully.")
            success = True
            break
        except Exception as e:
            print(f"[GDrive Error] Attempt {attempt}/{max_retries} failed to upload {filename}: {e}")
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
                
    if delete_local:
        try:
            os.remove(filename)
        except Exception as e:
            print(f"[GDrive Error] Failed to delete local file {filename}: {e}")

def download_file(file_name: str, dest_path: str) -> bool:
    """Download a file by name from the designated Google Drive folder."""
    try:
        service = get_drive_service()
        study_folder_id = _get_or_create_study_folder(service)
        query = f"'{study_folder_id}' in parents and name = '{file_name}' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        if not files:
            return False
            
        file_id = files[0]['id']
        request = service.files().get_media(fileId=file_id)
        
        with open(dest_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
        return True
    except Exception as e:
        print(f"[GDrive Error] Could not download {file_name}: {e}")
        return False

def get_completed_trials_file_map() -> dict[int, str]:
    """Retrieve file ID map of all completed parquet trials from Google Drive. Prefers extended data over normal."""
    try:
        service = get_drive_service()
        study_folder_id = _get_or_create_study_folder(service)
        # Search for both trial_..._data.parquet and extended_..._data.parquet
        query = f"'{study_folder_id}' in parents and name contains '_data.parquet' and trashed = false"
        
        # Need to handle pagination
        items = []
        page_token = None
        while True:
            results = service.files().list(q=query, fields="nextPageToken, files(id, name)", pageToken=page_token, pageSize=1000).execute()
            items.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        
        trial_file_map = {}
        extended_file_map = {}
        
        pattern_trial = re.compile(r"^trial_(\d+)_data\.parquet$")
        pattern_ext = re.compile(r"^extended_(\d+)_data\.parquet$")
        
        for item in items:
            match_ext = pattern_ext.match(item['name'])
            if match_ext:
                trial_id = int(match_ext.group(1))
                extended_file_map[trial_id] = item['id']
                continue
                
            match_trial = pattern_trial.match(item['name'])
            if match_trial:
                trial_id = int(match_trial.group(1))
                trial_file_map[trial_id] = item['id']
                
        # Merge maps, letting extended files override regular trials
        final_map = trial_file_map.copy()
        final_map.update(extended_file_map)
        return final_map
    except Exception as e:
        print(f"[GDrive Error] Could not retrieve file map: {e}")
        return {}

def get_all_filenames_in_folder() -> list[str]:
    """Retrieve list of names of all files in the designated Google Drive folder."""
    try:
        service = get_drive_service()
        study_folder_id = _get_or_create_study_folder(service)
        query = f"'{study_folder_id}' in parents and trashed = false"
        results = service.files().list(q=query, fields="files(name)", pageSize=1000).execute()
        items = results.get('files', [])
        return [item['name'] for item in items]
    except Exception as e:
        print(f"[GDrive Error] Could not retrieve file names: {e}")
        return []
