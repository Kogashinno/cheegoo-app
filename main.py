import os
import json
import datetime
import traceback
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from characters import characters, STAGE_RULES

app = Flask(__name__)

# Gemini APIキー設定
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

# スプレッドシート認証
def get_gsheet():
    try:
        creds_json = json.loads(os.environ["GSHEET_JSON"])
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
        client = gspread.authorize(creds)
        sheet = client.open("育成ログ").worksheet("育成ログ")
        status = client.open("育成ログ").worksheet("育成ステータス")
        return sheet, status
    except Exception as e:
        print("スプレッドシート接続エラー:", str(e))
        traceback.print_exc()
        return None, None

# ログ書き込み
def write_log(sheet, data):
    try:
        sheet.append_row(data)
    except Exception as e:
        print("ログ書き込み失敗:", str(e))
        traceback.print_exc()

# GP加算・ステータス更新
def update_status(status_sheet, uid, char_key):
    try:
        records = status_sheet.get_all_records()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        for i, row in enumerate(records, start=2):
            if row["uid"] == uid:
                last_date = row["最終グチ日"]
                if last_date != today:
                    gp = int(row["GP"]) + 10
                    status_sheet.update_cell(i, 2, gp)  # GP列
                    status_sheet.update_cell(i, 4, today)  # 最終グチ日列
                return
        # 新規ユーザー
        status_sheet.append_row([uid, 10, char_key, "初期", today, 1, 1])
    except Exception as e:
        print("ステータス更新エラー:", str(e))
        traceback.print_exc()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        user_text = data.get("user_text", "")
        char_key = data.get("char", "hikage")
        uid = data.get("uid", "unknown")
        stage = data.get("stage", "初期")

        char_data = characters.get(char_key)
        if not char_data:
            return jsonify({"reply": "キャラが見つからないよ。"})

        system_prompt = char_data["stages"][stage]["system"]

        model = genai.GenerativeModel(model_name="models/gemini-pro")
        convo = model.start_chat(history=[])
        convo.send_message(system_prompt)
        convo.send_message(user_text)
        reply = convo.last.text.strip()

        sheet, status_sheet = get_gsheet()
        if sheet and status_sheet:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_data = [timestamp, uid, char_key, user_text, reply]
            write_log(sheet, log_data)
            update_status(status_sheet, uid, char_key)

        return jsonify({"reply": reply})
    except Exception as e:
        print("全体処理エラー:", str(e))
        traceback.print_exc()
        return jsonify({"reply": "エラーが発生したよ。ログを確認してね。"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


