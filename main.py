import os
import json
import datetime
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import characters

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── Gemini API 初期化 ───────────────
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-pro-latest")

# ── Google Sheets 接続設定 ──────────
SHEET_ID = "1DStsyJeMGovNRV-dp6FxREFTEzZqwgwd5q20oqQNx4w"
LOG_SHEET = "育成ログ"
STATUS_SHEET = "育成ステータス"

def get_sheet(name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GSHEET_JSON"]), scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(name)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_text = data.get("text", "").strip()
    char_key = data.get("char", "hikage").lower()
    uid = data.get("uid", "user").strip()

    char_cfg = characters.characters.get(char_key, characters.characters["hikage"])
    stage = "初期"
    sys_prompt = char_cfg["stages"][stage]["system"]
    user_prompt = char_cfg["stages"][stage]["user"]
    prompt = f"{sys_prompt}\n{user_prompt.format(text=user_text)}"

    try:
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.6, "max_output_tokens": 150}
        )
        reply = response.text.strip()
    except Exception as e:
        reply = f"⚠ エラー: {str(e)}"

    got_gp = False
    try:
        log_ws = get_sheet(LOG_SHEET)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_ws.append_row([now, uid, char_key, user_text, reply])
    except Exception as e:
        print("⚠ 育成ログ保存失敗:", e)

    try:
        stat_ws = get_sheet(STATUS_SHEET)
        today = datetime.date.today()
        cell = stat_ws.find(uid)

        if cell is None:
            stat_ws.append_row([uid, char_key, stage, today.isoformat(), 1, 1, 10, today.isoformat()])
            got_gp = True
            reply += "\n（新規ユーザーとして登録されました）"
        else:
            row = cell.row
            values = stat_ws.row_values(row)

            streak = int(values[4]) if len(values) > 4 and values[4].isdigit() else 0
            total = int(values[5]) if len(values) > 5 and values[5].isdigit() else 0
            gp = int(values[6]) if len(values) > 6 and values[6].isdigit() else 0
            last_gp_date = values[7] if len(values) > 7 else ""
            last_day = values[3] if len(values) > 3 else ""

            if last_day != today.isoformat():
                diff = (today - datetime.datetime.strptime(last_day, "%Y-%m-%d").date()).days if last_day else 0
                streak = streak + 1 if diff == 1 else 1
                total += 1
                last_day = today.isoformat()

            if last_gp_date != today.isoformat():
                gp += 10
                last_gp_date = today.isoformat()
                got_gp = True

            stat_ws.update(f"C{row}:H{row}", [[stage, last_day, streak, total, gp, last_gp_date]])
    except Exception as e:
        print("⚠ ステータス更新失敗:", e)

    img_path = f"/static/images/{char_key}/{stage}.gif"

    return jsonify({
        "character": char_cfg["name"],
        "reply": reply + ("\n（+10 GP）" if got_gp else ""),
        "img": img_path
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
