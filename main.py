import os
import json
import datetime
import traceback
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# characters.pyからcharactersをインポートします。
# STAGE_RULESについては、もしcharacters.pyに定義がない場合は別途追加が必要です。
# 例として、このファイルの最後にSTAGE_RULESの定義を記載していますので、
# 必要に応じてcharacters.pyに移動させてください。
from characters import characters 

app = Flask(__name__)

# Gemini APIキー設定
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

# --- ここからデバッグ用の追加コード ---
# 利用可能なGeminiモデルをリストして確認
print("--- 利用可能なGeminiモデル一覧 ---")
try:
    for m in genai.list_models():
        # generateContent メソッドをサポートしているモデルのみ表示
        if "generateContent" in m.supported_generation_methods:
            print(f"利用可能モデル: {m.name}")
except Exception as e:
    print(f"モデルリストの取得中にエラーが発生しました: {e}")
print("--------------------------------")
# --- ここまでデバッグ用の追加コード ---

# Geminiモデルの初期化
# ログで確認できた利用可能なモデル名を使用します。
model = genai.GenerativeModel(model_name="models/gemini-1.5-pro-latest")


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

        # --- ここを修正しました ---
        # user_textから全角・半角スペース、改行などを全て取り除く
        user_text = "".join(user_text.split()) 
        
        # user_textが空の場合は、エラーを返さず処理を中断
        if not user_text: # .strip()を使わず、完全に空になったかを確認
            return jsonify({"reply": "何か入力してください。"})
        # --- 修正ここまで ---

        char_data = characters.get(char_key)
        if not char_data:
            return jsonify({"reply": "キャラが見つからないよ。"})

        system_prompt = char_data["stages"][stage]["system"]

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

# --- STAGE_RULESの定義（もしcharacters.pyに定義がない場合） ---
# もしcharacters.pyにSTAGE_RULESを移動させる場合は、このブロックは削除してください。
# main.pyにSTAGE_RULESを置くのは、通常推奨されるプラクティスではありません。
# characters.pyに置いて、そこからインポートするのがベストです。
try:
    from characters import STAGE_RULES
except ImportError:
    print("WARNING: STAGE_RULES was not found in characters.py. Using a default definition in main.py.")
    STAGE_RULES = {
        "初期": {"min_gp": 0, "condition": "誰でもここから。"},
        "中期": {"min_gp": 30, "condition": "GP30以上、または3日連続グチ。"},
        "後期_陽": {"min_gp": 60, "condition": "GP60以上、かつポジティブ率50%以上。"},
        "後期_陰": {"min_gp": 60, "condition": "GP60以上、かつポジティブ率50%未満。"},
        "特別_キラキラ": {"min_gp": 100, "condition": "後期ステージ到達、かつGP100以上。"},
        "特別_固有": {"min_gp": None, "condition": "後期ステージ到達、かつキャラ別条件達成。"}
    }
# --- STAGE_RULESの定義ここまで ---



