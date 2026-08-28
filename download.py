#!/usr/bin/env python3
import sys, os
import yt_dlp

def download_video(url, output_dir='public'):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    ydl_opts = {
        'outtmpl': f'{output_dir}/%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        # Se serve forzare referer:
        # 'http_headers': {'Referer': 'https://veezie.st/'}
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            print(f"DOWNLOADED: {filename}")
            return filename
    except Exception as e:
        print(f"ERROR: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python download.py <vixcloud_url>", file=sys.stderr)
        sys.exit(1)
    download_video(sys.argv[1])
