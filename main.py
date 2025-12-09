import os
import json
import time
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from openai import OpenAI
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
import io

# === 設定 (環境変数から取得) ===
CLIENT_ID = os.environ.get("G_CLIENT_ID")
CLIENT_SECRET = os.environ.get("G_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("G_REFRESH_TOKEN")
ROOT_FOLDER_ID = os.environ.get("ROOT_FOLDER_ID")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
LINE_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_TO = os.environ.get("LINE_TO_ID")

# OpenAI Client
client = OpenAI(api_key=OPENAI_KEY)

def get_drive_service():
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    )
    return build('drive', 'v3', credentials=creds)

def get_youtube_service():
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    return build('youtube', 'v3', credentials=creds)

def main():
    print("=== 処理開始 ===")
    drive = get_drive_service()
    
    # ルートフォルダ内のサブフォルダを検索
    query = f"'{ROOT_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = drive.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get('files', [])

    for folder in folders:
        # [Processed] が付いていないフォルダのみ対象
        if "[Processed]" in folder['name']:
            continue
            
        print(f"フォルダ検知: {folder['name']}")
        process_folder(drive, folder)

def process_folder(drive, folder):
    folder_id = folder['id']
    folder_name = folder['name']
    
    # フォルダ内のファイルをリスト
    query = f"'{folder_id}' in parents and trashed = false"
    items = drive.files().list(q=query, fields="files(id, name, mimeType)").execute().get('files', [])
    
    video_file = None
    transcript_file = None
    
    for item in items:
        if item['mimeType'] == 'video/mp4' or item['name'].endswith('.mp4'):
            video_file = item
        if item['name'] == 'closed_caption.txt' or item['name'].endswith('.vtt'):
            transcript_file = item
            
    if not transcript_file:
        print(f"字幕なし: {folder_name} - スキップ")
        return

    print("★ 字幕ダウンロード中...")
    transcript_text = drive.files().get_media(fileId=transcript_file['id']).execute().decode('utf-8')
    
    # 1. OpenAI 要約
    print("★ AI要約生成中...")
    summary = generate_summary(transcript_text)
    
    # 2. PDF作成 & Driveアップロード
    print("★ PDF作成中...")
    pdf_link = create_pdf_in_drive(drive, folder_id, folder_name, summary)

    # 3. YouTube アップロード (動画がある場合)
    youtube_link = "(動画なし)"
    if video_file:
        print(f"★ YouTubeへ動画転送中: {video_file['name']}")
        youtube_link = upload_video_to_youtube(drive, video_file)
    
    # 4. LINE通知
    print("★ LINE通知...")
    send_line(folder_name, pdf_link, youtube_link)
    
    # 5. フォルダ名を変更して処理済みにする
    new_name = f"[Processed] {folder_name}"
    drive.files().update(fileId=folder_id, body={'name': new_name}).execute()
    print(f"完了: {new_name}")

def generate_summary(text):
    # (プロンプトは以前の内容と同じものを設定)
    system_prompt = "あなたは、日本体育大学スポーツキュアセンター横浜・健志台接骨院および日本体育大学大学院の公式議事録作成者です。入力された「会議の字幕データ」を読み、内容に応じて自動で会議の種類を判定し、適切な形式で出力してください。強調は用いないでください．
【重要：要約レベルの指示】
1. 「大学院の進捗発表・抄読会」の場合（重要）：
   - 絶対に短くまとめすぎないでください。
   - 3時間の長丁場であっても、**「発表者ごと」**にセクションを分け、詳細を記述してください。
   - 専門用語は省略せず、研究の進捗や紹介された論文の内容を詳しく残してください。
   - 指導教員からのフィードバックや質疑応答は特に重要です。
       - 以下の先生が参加しています．
        - 伊藤先生（研究室の指導教員、柔道整復教育および生成AI研究のエキスパート）
        - 祁答院先生（高校生女性アスリートにおける睡眠研究）
        - 西尾先生（高齢者における健康寿命延伸のための研究）
        - 杉澤先生（生成AIを用いた柔道整復教育に関する研究）
        - 秋津先生（足関節捻挫に関する研究）
        - 二連木先生（オンライン学修効果）
        - 佐藤先生（学生のモチベーション変化）
        - **発表内容例**:
    - 研究の進捗状況
    - 今後の計画
    - 論文抄読に基づく議論


2. 「接骨院のミーティング」の場合：
【全体ミーティングの議題リスト】
1．実技チェックについて  
2．社会貢献推進事業・自治体連携協定推進事業  
3．初診患者用アンケート  
4．AT-miniの貸出サービスについて  
5．医師→キュア　紹介患者の進捗について  
6．スポーツドクター相談会  
7．月刊キュアについて  
8．卒研生の進捗状況  
9．来院件数報告について  
10．診療時間・受付時間について  
11．患者受診情報報告書・学友会別患者集計について  
12．アルバイト研修生について  
13．臨床実習について  
14．脱臼レポートについて  
15．患者さんを増やす取り組みについて  
16．ソックス開発について  
17．マイクロカレントの研究について  
18．学会発表  
19．AR-Exクラーク研修について  
20．臨時議題（上記に該当しないもの）

【スタッフミーティング】
- 上記の全体ミーティング項目に該当しない、または現場運営・日常業務に関する内容を議題として整理する。"
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        temperature=0.3
    )
    return response.choices[0].message.content

def upload_video_to_youtube(drive, file_info):
    youtube = get_youtube_service()
    
    # 動画ファイルを一時的にローカルにダウンロード
    # (GitHub Actionsは数GBのディスク容量があるので大丈夫です)
    request = drive.files().get_media(fileId=file_info['id'])
    fh = io.FileIO("temp_video.mp4", "wb")
    downloader = MediaIoBaseUpload(fh, mimetype="video/mp4")
    
    # 注: 大きなファイル用にDownloaderを使う実装もありますが、
    # 簡単のため一旦request.execute()でバイナリ取得して保存します
    # ファイルが巨大すぎる(2GB超)場合はチャンクダウンロードが必要
    file_content = request.execute() 
    with open("temp_video.mp4", "wb") as f:
        f.write(file_content)
        
    # YouTubeへアップロード
    body = {
        'snippet': {
            'title': file_info['name'],
            'description': 'Automated Upload from Drive',
            'categoryId': '22'
        },
        'status': {
            'privacyStatus': 'unlisted' # 限定公開
        }
    }
    
    media = MediaFileUpload("temp_video.mp4", chunksize=1024*1024, resumable=True)
    request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)
    
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")
            
    # 一時ファイル削除
    os.remove("temp_video.mp4")
    
    return f"https://youtu.be/{response['id']}"

def create_pdf_in_drive(drive, folder_id, title, text):
    # 簡易的なテキストファイルとして保存（PDF化は日本語フォント設定が複雑なため、まずはテキスト保存を推奨）
    # もしPDF必須であればreportlabでフォント読み込みが必要ですが、
    # ここでは一番確実な「Googleドキュメント作成」ではなく「テキストファイル」または「Markdown」で保存します
    
    file_metadata = {
        'name': f'議事録_{title}.txt',
        'parents': [folder_id],
        'mimeType': 'text/plain'
    }
    
    media = MediaIoBaseUpload(io.BytesIO(text.encode('utf-8')), mimetype='text/plain')
    file = drive.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
    
    # 権限設定（リンクを知っている人全員）
    drive.permissions().create(
        fileId=file['id'],
        body={'role': 'reader', 'type': 'anyone'},
    ).execute()
    
    return file['webViewLink']

def send_line(title, doc_url, video_url):
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    msg = f"\n【議事録完了】\n会議名: {title}\n\n📝 議事録:\n{doc_url}\n\n🎬 YouTube:\n{video_url}"
    payload = {"message": msg, "to": LINE_TO} # Pushの場合はAPIが変わりますがNotifyならこれ
    
    # Messaging API (Push) の場合
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "to": LINE_TO,
        "messages": [{"type": "text", "text": msg.strip()}]
    }
    requests.post(url, headers=headers, json=data)

if __name__ == "__main__":
    main()
