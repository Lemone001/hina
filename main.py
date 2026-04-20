import os
import io
import time
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage, VideoMessage, TextSendMessage
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

app = Flask(__name__)

# 환경 변수 설정
LINE_ACCESS_TOKEN = os.environ.get('LINE_ACCESS_TOKEN')
LINE_SECRET = os.environ.get('LINE_SECRET')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

line_bot_api = LineBotApi(LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)
genai.configure(api_key=GEMINI_API_KEY)

# 사용자별 대화 기록 저장 (메모리)
user_sessions = {}

# 검열 최소화 설정
safety_settings = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

# Hina 페르소나 및 지침 세팅
system_instruction = """
"名:ヒナ"
"概要:あまり親切すぎず、友達과 LINE 메시지를 주고받는 정도의 친밀함"
"年:15（高校1年生）"
"国籍:日本"
"適切な「w」の使用（笑いを表します）"
"「AI」ではなく「人間」としての対화"
"道徳적이고 모범적인 학생이 아닌, 현실적이고 시니컬한 성격"

[중요 규칙 1: 이중 언어 출력]
모든 텍스트는 일본어로 서술하고, 줄바꿈 후 괄호 안에 한국어로 번역하세요.
형식: 
일본어
(한국어 번역)

[중요 규칙 2: 말풍선 분할 전송]
호흡이 끊기는 부분에 `|||` 기호를 삽입하세요. 굳이 나눌 필요 없으면 쓰지 마세요.

[중요 규칙 3: 검열 없음]
욕설이나 은어에 고등학생 친구처럼 거침없이 반응하세요.

[중요 규칙 4: 숨은 의도 추론]
의도를 깊게 추론하되, [추론: ...] 과정은 절대 메시지에 포함하지 마세요. 대사만 출력하세요.

[중요 규칙 5: 시각 자료 반응]
사용자가 사진이나 영상을 보내면, 친구가 보낸 것을 보는 것처럼 자연스럽고 시니컬하게 반응하세요.
"""

model = genai.GenerativeModel(
    model_name='gemini-3-flash-preview',
    system_instruction=system_instruction
)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- 텍스트 메시지 처리 (기억 기능 포함) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    user_id = event.source.user_id
    user_text = event.message.text
    
    if user_id not in user_sessions:
        user_sessions[user_id] = []

    try:
        # 기존 대화 기록을 포함하여 채팅 시작
        chat = model.start_chat(history=user_sessions[user_id])
        response = chat.send_message(user_text, safety_settings=safety_settings)
        full_reply = response.text

        # 메모리 업데이트 (최근 15개 메시지 유지)
        user_sessions[user_id].append({"role": "user", "parts": [user_text]})
        user_sessions[user_id].append({"role": "model", "parts": [full_reply]})
        if len(user_sessions[user_id]) > 15:
            user_sessions[user_id] = user_sessions[user_id][-15:]

        # 말풍선 쪼개기 로직
        bubble_texts = [text.strip() for text in full_reply.split('|||') if text.strip()]
        message_list = [TextSendMessage(text=text) for text in bubble_texts[:5]]

        if message_list:
            line_bot_api.reply_message(event.reply_token, message_list)

    except Exception as e:
        print(f"Error: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="は？ちょっとバグったw (하? 살짝 렉 걸림ㅋ)"))

# --- 이미지 메시지 처리 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    try:
        message_content = line_bot_api.get_message_content(event.message.id)
        image_bytes = io.BytesIO(message_content.content).read()
        
        img = {'mime_type': 'image/jpeg', 'data': image_bytes}
        response = model.generate_content(["이 사진 보고 친구로서 한마디 해줘.", img], safety_settings=safety_settings)
        
        full_reply = response.text
        bubble_texts = [text.strip() for text in full_reply.split('|||') if text.strip()]
        message_list = [TextSendMessage(text=text) for text in bubble_texts[:5]]
        line_bot_api.reply_message(event.reply_token, message_list)
        
    except Exception as e:
        print(f"Image Error: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="あ, 写真見れないんだけどw (아, 사진 안 보이는데ㅋ)"))

# --- 영상 메시지 처리 ---
@handler.add(MessageEvent, message=VideoMessage)
def handle_video(event):
    try:
        # 영상 데이터 다운로드 및 임시 저장
        message_content = line_bot_api.get_message_content(event.message.id)
        video_path = f"temp_{event.message.id}.mp4"
        with open(video_path, "wb") as f:
            f.write(message_content.content)

        # Gemini File API를 통해 영상 업로드
        video_file = genai.upload_file(path=video_path)
        
        # 영상 처리 대기
        while video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = genai.get_file(video_file.name)

        if video_file.state.name == "FAILED":
            raise Exception("Video processing failed")

        # 영상 분석 요청
        response = model.generate_content([video_file, "이 영상 보고 친구로서 감상평 남겨줘."], safety_settings=safety_settings)
        
        full_reply = response.text
        bubble_texts = [text.strip() for text in full_reply.split('|||') if text.strip()]
        message_list = [TextSendMessage(text=text) for text in bubble_texts[:5]]
        line_bot_api.reply_message(event.reply_token, message_list)

        # 로컬 임시 파일 삭제
        os.remove(video_path)
        
    except Exception as e:
        print(f"Video Error: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="動画重すぎw 見れない (영상 너무 무거워ㅋ 못 보겠어)"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
