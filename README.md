# 🎬 yt-auto — Automated YouTube Video Pipeline

> A production-ready, fully-automated YouTube educational video pipeline. It automatically generates and schedules YouTube Shorts and long-form videos with zero ongoing human work.

---

## 🌟 How It Works

This system runs a fully-automated, schedules-based video creation and upload workflow:

```
[ Scheduled GitHub Action / manual run ]
   ├── Topic Detection (Gemini 2.5 Flash + Google Search Grounding)
   ├── Script Writing & Grounded Fact-Verification
   ├── Voice Generation (Gemini TTS / Fallback Kokoro CPU)
   ├── Video/Image Retrieval (Pexels / Pixabay / Fallback Pollinations AI)
   ├── Timing-based Subtitles (word-level timestamps)
   ├── Background Music (Procedural Numpy Synth / pad chords)
   ├── SFX (Whoosh transitions synced to cuts)
   ├── FFmpeg Assembly & Auto-looping logic (Using superfast preset)
   ├── Video Thumbnail Generation (1280x720 overlay)
   └── Auto-Publish to YouTube (Public, with auto hashtags, containsSyntheticMedia flag)
```

---

## 🤖 AI Developer & Agent Memory (READ THIS BEFORE EDITING)

If you are an AI agent or developer touching this codebase, review these critical architectural decisions, constraints, and lessons learned from past implementations:

### 1. Gemini API Key Rotation & 503/429 Resiliency
* **Location:** [pipeline/gemini.py](file:///root/yt-auto/pipeline/gemini.py) (`_post_with_rotation` and `_KeyPool`)
* **Experience Gained:** Relying on a single API key easily hits daily quotas or rate limits (`429`). The Gemini API also frequently throws `503 Service Unavailable` errors under heavy load.
* **Mechanism:** 
  * The environment variable `GEMINI_API_KEYS` accepts a comma-separated list of keys (e.g. `key1,key2,key3`).
  * On a `429` error, the pool rotates immediately to the next key without delaying execution.
  * On a `500`/`502`/`503`/`504` error, the pool rotates and sleeps for 2 seconds before retrying.
  * Pinned key operations (e.g. for the video Judge) fallback to linear/exponential backoff.

### 2. FFmpeg Assembly & Rendering Speed
* **Location:** [pipeline/phase7_assemble.py](file:///root/yt-auto/pipeline/phase7_assemble.py)
* **Experience Gained:** In CPU-bound environments (like GitHub Actions runners or low-resource servers), rendering video with slow presets or complete transcoding takes hours.
* **Mechanism:** 
  * Re-encoding uses the `superfast` preset.
  * Muxing streams (final combining of audio, video, and effects) uses `-c:v copy` and `-c:a aac` wherever possible to avoid redundant encoding.
  * Subtitles/captions are applied dynamically via a generated `.ass` file overlay.

### 3. YouTube Upload Policies & SEO Automation
* **Location:** [pipeline/phase9_upload.py](file:///root/yt-auto/pipeline/phase9_upload.py)
* **Privacy Status:** Uploads are set to `"public"` so that the video goes live immediately upon completion (or is scheduled with `publishAt`).
* **SEO Hashtags:** The upload step parses tags from metadata, converts them to `#Hashtag` format, and appends them to the description automatically.
* **Synthetic Media Disclosure (MANDATORY):** Per YouTube’s global policy, AI-generated content must declare synthetic media. We explicitly pass `"containsSyntheticMedia": True` in the `status` block. *Do not disable this to prevent channel strikes.*

### 4. Custom Thumbnail Upload Verification
* **Location:** [pipeline/phase9_upload.py](file:///root/yt-auto/pipeline/phase9_upload.py) (Thumbnail call)
* **Gotcha:** If a channel is not phone-verified in YouTube Studio (**Settings** ➔ **Channel** ➔ **Feature Eligibility**), the API call to upload a custom thumbnail will fail. 
* **Handling:** The script catches thumbnail errors gracefully so the video still publishes successfully even if custom thumbnails are disabled on the channel.

### 5. Runtime & Persistent Server Context
* **Reality Check:** GitHub Actions has a 6-hour job execution limit. However, since our generation and publish steps take less than 10 minutes total per video, this limit is not an issue.
* **WhatsApp Bot Co-existence:** The WhatsApp bot (`guri-v10-webjs` / `guri_bot`) is hosted on Termux/Railway and runs 24/7. **Do not confuse its hosting requirements with the short-lived GitHub Actions pipeline.**

---

## 🔑 GitHub Secrets Setup

Configure the following repository secrets under **Settings** ➔ **Secrets and variables** ➔ **Actions**:

| Secret Name | Required | Description / Value |
| :--- | :--- | :--- |
| `GEMINI_API_KEYS` | **Yes** | Comma-separated list of Google AI Studio keys for rotation. |
| `GEMINI_API_KEY` | **Yes** | Single fallback Google AI Studio key. |
| `PEXELS_API_KEY` | **Yes** | Used to fetch background B-roll videos. |
| `PIXABAY_API_KEY`| No | Optional fallback B-roll api. |
| `YT_CLIENT_ID` | **Yes** | OAuth 2.0 Client ID. |
| `YT_CLIENT_SECRET`| **Yes** | OAuth 2.0 Client Secret. |
| `YT_REFRESH_TOKEN`| **Yes** | OAuth 2.0 Refresh Token (authorized for YouTube scope). |

---

## ⚙️ One-Time Setup Instructions

### 1. YouTube OAuth Consent Screen Fix
If your credentials fail with authentication errors, ensure your app is published:
1. Go to the [Google Cloud Console](https://console.cloud.google.com).
2. Navigate to **APIs & Services** ➔ **OAuth consent screen**.
3. Under **Publishing status**, click **Publish App** to move it out of "Testing" mode. This prevents refresh tokens from expiring after 7 days.

### 2. Getting a Refresh Token
If you need to generate a new refresh token:
1. Go to the [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground).
2. Click the gear icon (top right), check **Use your own OAuth credentials**, and input your `YT_CLIENT_ID` and `YT_CLIENT_SECRET`.
3. In Step 1, select the scope `https://www.googleapis.com/auth/youtube` and click **Authorize APIs**.
4. Log in, grant permissions, click **Exchange authorization code for tokens**, and copy the `refresh_token`.

---

## ⏱️ Video Publishing Schedule

The automation is configured around Indian Standard Time (IST):

*   **Short #1:** Uploads daily at **10:00 AM IST** ➔ Schedules to publish at **12:00 PM IST (Noon)**.
*   **Short #2:** Uploads daily at **05:00 PM IST** ➔ Schedules to publish at **07:00 PM IST (Evening)**.
*   **Long-form:** Uploads every Monday at **11:30 AM IST** ➔ Schedules to publish at **02:00 PM IST**.
