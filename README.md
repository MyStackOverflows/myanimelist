# MyAnimeList
Simple python script that offers a simple centralized method of bulk checking if anime you're interested in have finished airing. Also includes functionality for searching torrents using qBittorrent.

### Pre-requisites
 - Python 3.10+
 - `qbittorrent-api` package

### Config files
 - `secret_app_info.json`: app configuration file of the form:
    ```
    {
        "CLIENT_ID": "your client id as found on your account's api page"
    }
    ```
 - `secret_token.json`: mal api auth configuration file of the form (note: `refresh_token.py` will generate this file for you):
    ```
    {
        "token_type": "Bearer",
        "expires_in": 2678400,
        "access_token": "your_access_token",
        "refresh_token": "your_refresh_token"
    }
    ```

### To use qBittorrent functionality, these environment variables MUST be set:
 - `QBITTORRENTAPI_HOST`: `qbittorrent_server_ip:port`
 - `QBITTORRENTAPI_USERNAME`: `your_username`
 - `QBITTORRENTAPI_PASSWORD`: `your_password`