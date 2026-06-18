from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from pipeline.config import YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN

def upload_to_youtube(video_path: str, thumbnail_path: str, metadata: dict) -> str:
    print("Initializing YouTube API client...")
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube"]
    )
    
    # Refresh the credentials
    creds.refresh(Request())
    youtube = build("youtube", "v3", credentials=creds)
    
    # Trim metadata arrays/strings to fit within YouTube API constraints and strip angle brackets
    title = metadata.get("title", "Educational Video")[:100].replace('<', '').replace('>', '')
    description = metadata.get("description", "Fast. Accurate. Mind-blowing.")[:5000].replace('<', '').replace('>', '')
    tags = metadata.get("tags", [])
    # Convert tags to a list of strings and truncate
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = [str(t)[:100].replace('<', '').replace('>', '') for t in tags][:500]

    # Append hashtags from tags to the description for YouTube discoverability
    hashtags = " ".join(f"#{t.replace(' ', '')}" for t in tags[:15])
    if hashtags:
        # Ensure there's a blank line before the hashtag block
        if not description.endswith("\n"):
            description += "\n"
        description += f"\n{hashtags}"
    
    category_id = metadata.get("category_id", "27")
    
    status_body = {
        "privacyStatus":          "public",         # Upload as public for immediate views
        "selfDeclaredMadeForKids": False,
        "madeForKids":            False,
        "containsSyntheticMedia": True,             # MANDATORY — May 2026 YouTube policy
    }
    
    # Only include publishAt if it's set and not None
    publish_at = metadata.get("publish_at")
    if publish_at:
        status_body["publishAt"] = publish_at
        
    body = {
        "snippet": {
            "title":                title,
            "description":          description,
            "tags":                 tags,
            "categoryId":           category_id,
            "defaultLanguage":      "en",
            "defaultAudioLanguage": "en",
        },
        "status": status_body
    }
    
    print(f"Uploading video {video_path} to YouTube...")
    # Resumable upload in 5MB chunks
    media = MediaFileUpload(video_path, chunksize=5*1024*1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
            
    video_id = response["id"]
    print(f"Video uploaded successfully. Video ID: {video_id}")
    
    # Set thumbnail
    print(f"Setting thumbnail {thumbnail_path} for video {video_id}...")
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
        ).execute()
        print("Thumbnail updated successfully.")
    except Exception as e:
        print(f"Warning: Failed to upload thumbnail: {e}")
        
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"Uploaded: {url}")
    return video_id
