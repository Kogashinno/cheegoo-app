import os
import json
import datetime
import traceback
import logging # loggingモジュールをインポート
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# characters.pyからcharactersとSTAGE_RULESをインポートします。
# STAGE_RULESがcharacters.pyに定義されていることを推奨します。
# もしcharacters.pyに定義がない場合は、このファイルの最後にあるデフォルト定義が使用されます。
from characters import characters, STAGE_RULES 

app = Flask(__name__)

# --- ロギング設定の追加 ---
# ロガーのレベルを設定 (INFOレベル以上のログを出力)
app.logger.setLevel(logging.INFO)
# コンソールにもログを出力するようにハンドラを設定（Renderはこれを拾います）
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)
# --- ロギング設定ここまで ---

# Gemini APIキー設定
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

# --- ここからデバッグ用の追加コード ---
# 利用可能なGeminiモデルをリストして確認
app.logger.info("--- 利用可能なGeminiモデル一覧 ---")
try:
    for m in genai.list_models():
        # generateContent メソッドをサポートしているモデルのみ表示
        if "generateContent" in m.supported_generation_methods:
            app.logger.info(f"利用可能モデル: {m.name}")
except Exception as e:
    app.logger.error(f"モデルリストの取得中にエラーが発生しました: {e}")
app.logger.info("--------------------------------")
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
        app.logger.error("スプレッドシート接続エラー: %s", str(e))
        traceback.print_exc()
        return None, None

# ログ書き込み
def write_log(sheet, data):
    try:
        sheet.append_row(data)
    except Exception as e:
        app.logger.error("ログ書き込み失敗: %s", str(e))
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
                    # 既存ユーザーのGPと最終グチ日を更新
                    gp = int(row["GP"]) + 10
                    # 列のインデックスはスプレッドシートの実際の列順に合わせる
                    # 画像から7列目: GP, 4列目: 最終グチ日 と推測
                    status_sheet.update_cell(i, 7, gp)  # GP列
                    status_sheet.update_cell(i, 4, today)  # 最終グチ日列
                # グチ回数も更新する場合
                # gutsu_count = int(row["グチ回数"]) + 1
                # status_sheet.update_cell(i, 5, gutsu_count) # グチ回数 (画像から5列目と推測)
                return
        # 新規ユーザー
        # スプレッドシートの列順に合わせてデータを追加
        # 列: uid, char_key, 開始ステージ, 最終グチ日, グチ回数, ポチ数, GP
        new_user_data = [
            uid,           # uid (1列目)
            char_key,      # char_key (2列目)
            "初期",        # 開始ステージ (3列目)
            today,         # 最終グチ日 (4列目)
            1,             # グチ回数 (5列目) - 初回なので1
            1,             # ポチ数 (6列目) - 初回なので1 (もし不要なら0)
            10             # GP (7列目) - 初回なので10
        ]
        status_sheet.append_row(new_user_data)
    except Exception as e:
        app.logger.error("ステータス更新エラー: %s", str(e))
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

        # user_textから全角・半角スペース、改行などを全て取り除く
        user_text = "".join(user_text.split()) 
        
        # 受信したuser_textの中身をそのままログに出力して確認
        app.logger.info(f"--- 受信したuser_text ---: '{user_text}' (長さ: {len(user_text)})")

        # 空白除去後のuser_textの中身をログに出力して確認
        app.logger.info(f"--- 処理後のuser_text ---: '{user_text}' (長さ: {len(user_text)})")

        # user_textが空の場合は、エラーを返さず処理を中断
        if not user_text: 
            return jsonify({"reply": "何か入力してください。"})

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
        app.logger.error("全体処理エラー: %s", str(e))
        traceback.print_exc()
        return jsonify({"reply": "エラーが発生したよ。ログを確認してね。"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- STAGE_RULESの定義（もしcharacters.pyに定義がない場合） ---
# characters.pyにSTAGE_RULESを移動させる場合は、このブロックは削除してください。
# main.pyにSTAGE_RULESを置くのは、通常推奨されるプラクティスではありません。
# characters.pyに置いて、そこからインポートするのがベストです。
# ただし、characters.pyからインポートできない場合のフォールバックとして残します。
try:
    # 既に最上部でインポートを試みているので、ここでは追加のインポートは不要
    # from characters import STAGE_RULES # この行は重複するのでコメントアウト
    pass # 何もしない
except ImportError:
    # STAGE_RULESがcharacters.pyに見つからなかった場合のデフォルト定義
    app.logger.warning("STAGE_RULES was not found in characters.py. Using a default definition in main.py.")
    STAGE_RULES = {
        "初期": {"min_gp": 0, "condition": "誰でもここから。"},
        "中期": {"min_gp": 30, "condition": "GP30以上、または3日連続グチ。"},
        "後期_陽": {"min_gp": 60, "condition": "GP60以上、かつポジティブ率50%以上。"},
        "後期_陰": {"min_gp": 60, "condition": "GP60以上、かつポジティブ率50%未満。"},
        "特別_キラキラ": {"min_gp": 100, "condition": "後期ステージ到達、かつGP100以上。"},
        "特別_固有": {"min_gp": None, "condition": "後期ステージ到達、かつキャラ別条件達成。"}
    }
# --- STAGE_RULESの定義ここまで ---




