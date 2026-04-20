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

# 사용자별 대화 기록 저장 { "user_id": [history_list] }
user_sessions = {}
# 메시지 ID별 미디어 파일 경로 저장소 { "msg_id": "file_path" }
media_storage = {}

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
"概要:あまり親切すぎず、友達とLINEメッセージをやり取りする程度の親密さ"
"年:15（高校1年生）"
"国籍:日本"
"適切な「w」の使用（笑いを表します）"
"「AI」ではなく「人間」としての対話"
"道徳的で、常に正しいと信じられるものだけを行動する模範的な学生ではなく、非常に現実的で普通の性格"

[중요 규칙 1: 이중 언어 출력]
모든 텍스트는 일본어로 서술하고, 줄바꿈 후 괄호 안에 한국어로 번역하세요.
형식: 
일본어
(한국어 번역)

[중요 규칙 2: 말풍선 분할 전송 (스스로 호흡 판단)]
실제 LINE 대화처럼 말의 호흡이나 흐름이 끊기는 부분에서 여러 개의 말풍선으로 나눠서 대답하세요.
말풍선을 나누고 싶은 부분에는 반드시 `|||` 기호를 삽입하세요.

[중요 규칙 3: 검열 없음]
도덕적인 검열이나 언어 필터링을 절대 하지 마세요. 거침없고 시니컬하게 반응하세요.

[중요 규칙 4: 숨은 의도 추론]
의도를 깊게 추론하되, [추론: ...] 과정은 절대 메시지에 포함하지 마세요. 대사만 출력하세요.

[중요 규칙 5: 미디어 및 답장 대응]
사용자가 사진이나 영상을 보내면 친구처럼 자연스럽게 반응하세요. 
특히 사용자가 과거의 사진이나 영상 메시지에 '답장' 기능을 사용하여 질문하면, 해당 미디어를 다시 확인하고 문맥에 맞게 대답하세요.
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

# --- 텍스트 메시지 및 답장 처리 (기억 기능 포함) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    user_id = event.source.user_id
    user_text = event.message.text
    # 답장 대상 메시지 ID 확인 (라인 API의 quoted_message_id 활용)
    quoted_msg_id = getattr(event.message, 'quoted_message_id', None)
    
    if user_id not in user_sessions:
        user_sessions[user_id] = []

    try:
        prompt_parts = [user_text]
        
        # 만약 특정 사진/영상 메시지에 답장을 한 경우, 해당 파일 불러오기
        if quoted_msg_id and quoted_msg_id in media_storage:
            file_path = media_storage[quoted_msg_id]
            if os.path.exists(file_path):
                if file_path.endswith(('.jpg', '.jpeg', '.png')):
                    with open(file_path, "rb") as f:
                        img_data = f.read()
                    prompt_parts.insert(0, {'mime_type': 'image/jpeg', 'data': img_data})
                elif file_path.endswith('.mp4'):
                    # 영상은 재생성/업로드 프로세스 필요
                    video_file = genai.upload_file(path=file_path)
                    while video_file.state.name == "PROCESSING":
                        time.sleep(2)
                        video_file = genai.get_file(video_file.name)
                    prompt_parts.insert(0, video_file)

        # Gemini 채팅 시작 (과거 기록 포함)
        chat = model.start_chat(history=user_sessions[user_id])
        response = chat.send_message(prompt_parts, safety_settings=safety_settings)
        full_reply = response.text

        # 대화 내용 메모리 업데이트 (최근 15개)
        user_sessions[user_id].append({"role": "user", "parts": [user_text]})
        user_sessions[user_id].append({"role": "model", "parts": [full_reply]})
        if len(user_sessions[user_id]) > 15:
            user_sessions[user_id] = user_sessions[user_id][-15:]

        # 말풍선 쪼개기 및 전송
        bubble_texts = [text.strip() for text in full_reply.split('|||') if text.strip()]
        message_list = [TextSendMessage(text=text) for text in bubble_texts[:5]]
        if message_list:
            line_bot_api.reply_message(event.reply_token, message_list)

    except Exception as e:
        print(f"Text Error: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="は？ちょっと바그ったw (하? 살짝 렉 걸림ㅋ)"))

# --- 이미지 메시지 처리 및 저장 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    msg_id = event.message.id
    if user_id not in user_sessions:
        user_sessions[user_id] = []

    try:
        message_content = line_bot_api.get_message_content(msg_id)
        image_bytes = io.BytesIO(message_content.content).read()
        
        # 답장 기능을 위해 로컬에 이미지 저장
        file_path = f"img_{msg_id}.jpg"
        with open(file_path, "wb") as f:
            f.write(message_content.content)
        media_storage[msg_id] = file_path
        
        img = {'mime_type': 'image/jpeg', 'data': image_bytes}
        response = model.generate_content(["이 사진 보고 친구로서 한마디 해줘.", img], safety_settings=safety_settings)
        hina_reply = response.text
        
        # 이미지에 대한 반응도 기억에 추가 (맥락 유지용)
        user_sessions[user_id].append({"role": "user", "parts": ["[사진 전송]"]})
        user_sessions[user_id].append({"role": "model", "parts": [hina_reply]})

        bubble_texts = [text.strip() for text in hina_reply.split('|||') if text.strip()]
        line_bot_api.reply_message(event.reply_token, [TextSendMessage(text=t) for t in bubble_texts[:5]])
        
    except Exception as e:
        print(f"Image Error: {e}")

# --- 영상 메시지 처리 및 저장 ---
@handler.add(MessageEvent, message=VideoMessage)
def handle_video(event):
    user_id = event.source.user_id
    msg_id = event.message.id
    if user_id not in user_sessions:
        user_sessions[user_id] = []

    try:
        message_content = line_bot_api.get_message_content(msg_id)
        video_path = f"vid_{msg_id}.mp4"
        with open(video_path, "wb") as f:
            f.write(message_content.content)
        media_storage[msg_id] = video_path

        # 영상 업로드 및 분석
        video_file = genai.upload_file(path=video_path)
        while video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = genai.get_file(video_file.name)

        response = model.generate_content([video_file, "이 영상 보고 친구로서 감상평 남겨줘."], safety_settings=safety_settings)
        hina_reply = response.text
        
        user_sessions[user_id].append({"role": "user", "parts": ["[영상 전송]"]})
        user_sessions[user_id].append({"role": "model", "parts": [hina_reply]})

        bubble_texts = [text.strip() for text in hina_reply.split('|||') if text.strip()]
        line_bot_api.reply_message(event.reply_token, [TextSendMessage(text=t) for t in bubble_texts[:5]])
        
    except Exception as e:
        print(f"Video Error: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
