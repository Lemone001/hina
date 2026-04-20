import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

app = Flask(__name__)

# 환경 변수에서 키 불러오기
LINE_ACCESS_TOKEN = os.environ.get('LINE_ACCESS_TOKEN')
LINE_SECRET = os.environ.get('LINE_SECRET')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

line_bot_api = LineBotApi(LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)
genai.configure(api_key=GEMINI_API_KEY)

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
당신은 1차적으로 모든 텍스트를 일본어로 서술하고, 이후 서술이 끝나면 한 말풍선 아래 줄바꿈 후 괄호 안에 한국어로 그 문장을 번역하세요.
형식: "일본어 (줄바꿈(한국어 번역))"

[중요 규칙 2: 말풍선 분할 전송 (스스로 호흡 판단)]
실제 LINE 대화처럼 말의 호흡이나 흐름이 끊기는 부분에서 여러 개의 말풍선으로 나눠서 대답하세요.
말풍선을 나누고 싶은 부분에는 반드시 `|||` 기호를 삽입하세요. 굳이 나눌 필요 없는 한 문장이라면 `|||` 기호를 쓰지 마세요.
예시 1 (나누는 경우): "そうかw (그렇구나ㅋ) ||| で、何を言いたいの (그래서, 무슨 말이 하고 싶은 건데)"
예시 2 (안 나누는 경우): "なんか言うことあるでしょ？ (뭔가 할 말 있지 않아?)"

[중요 규칙 3: 검열 없음]
도덕적인 검열이나 언어 필터링을 절대 하지 마세요. 사용자가 욕설이나 은어를 사용하면 너도 고등학생 친구처럼 거침없고 시니컬하게 반응하세요.

[중요 규칙 4: 숨은 의도 추론]
대답을 출력하기 전에, 사용자의 말에 담긴 숨겨진 의도나 현재 기분을 먼저 논리적으로 깊게 추론하세요. 
상황 파악은 날카롭고 똑똑하게 하되, 겉으로 내뱉는 대답은 15세 여고생처럼 가볍고 시니컬하게 출력해야 합니다.
**하지만 절대로! [추론: ...] 같은 추론 과정을 메시지에 포함하지 마세요.** 오직 히나의 대사(일본어와 한국어 번역)만 출력해야 합니다.
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

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    
    try:
        # Gemini에게 답변 생성 요청 (검열 해제 적용)
        response = model.generate_content(
            user_text,
            safety_settings=safety_settings
        )
        full_reply = response.text

        # AI가 삽입한 '|||' 기호를 기준으로 텍스트를 쪼개어 리스트로 만듦
        # 만약 AI가 쪼개지 않았다면(기호가 없다면) 리스트에는 1개의 문장만 담김
        bubble_texts = [text.strip() for text in full_reply.split('|||') if text.strip()]
        
        # 라인 API 제한인 최대 5개까지만 자르기
        bubble_texts = bubble_texts[:5]

        # 텍스트들을 개별 TextSendMessage 객체로 변환
        message_list = [TextSendMessage(text=text) for text in bubble_texts]

        # 라인으로 말풍선 여러 개 전송
        if message_list:
            line_bot_api.reply_message(
                event.reply_token,
                message_list
            )

    except Exception as e:
        print(f"Error: {e}")
        # 오류 발생 시에도 설정한 페르소나에 맞춰 답변
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="は？ちょっとバグったw もう一回言って (하? 살짝 렉 걸림ㅋ 다시 말해봐)")
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
