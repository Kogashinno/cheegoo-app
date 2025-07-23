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
        
        # 既存ユーザーのデータを検索
        user_found = False
        for i, row in enumerate(records, start=2): # ヘッダー行を考慮して2から開始
            if str(row.get("uid")) == str(uid): # uidは文字列として比較
                user_found = True
                
                # スプレッドシートの列名と対応するPythonのキーを使用
                current_gp = int(row.get("GP", 0)) # GPがない場合は0として扱う
                last_grumble_date = row.get("最終グチ日")
                consecutive_grumble_days = int(row.get("グチ連続日数", 0))
                total_grumble_count = int(row.get("総グチ数", 0))
                
                # GP加算（日付が変わった場合のみ）
                if last_grumble_date != today:
                    current_gp += 10
                    consecutive_grumble_days += 1 # 連続グチ日数を加算
                    # GPと最終グチ日、グチ連続日数、最終GP付与日を更新
                    status_sheet.update_cell(i, 7, current_gp)  # GP列 (G列)
                    status_sheet.update_cell(i, 4, today)      # 最終グチ日列 (D列)
                    status_sheet.update_cell(i, 5, consecutive_grumble_days) # グチ連続日数 (E列)
                    status_sheet.update_cell(i, 8, today)      # 最終GP付与日列 (H列)
                
                total_grumble_count += 1 # 総グチ数は毎回加算
                status_sheet.update_cell(i, 6, total_grumble_count) # 総グチ数 (F列)
                
                # 現在ステージの更新ロジックは別途実装が必要
                # 例: status_sheet.update_cell(i, 3, new_stage_value) # 現在ステージ (C列)
                return # ユーザーが見つかり更新したら終了

        # 新規ユーザー
        if not user_found:
            # スプレッドシートの列順に合わせてデータを追加
            # 列: uid (A), char_key (B), 現在ステージ (C), 最終グチ日 (D), グチ連続日数 (E), 総グチ数 (F), GP (G), 最終GP付与日 (H)
            new_user_data = [
                uid,                   # A列
                char_key,              # B列
                "初期",                # C列 (現在ステージ)
                today,                 # D列 (最終グチ日)
                1,                     # E列 (グチ連続日数 - 初回なので1)
                1,                     # F列 (総グチ数 - 初回なので1)
                10,                    # G列 (GP - 初回なので10)
                today                  # H列 (最終GP付与日 - 初回なので今日)
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
        stage = data.get("stage", "初期") # フロントエンドからステージが送られてくる場合

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

        # 現在のステージのシステムプロンプトを取得
        # stage変数がフロントエンドから送られてくることを前提
        system_prompt = char_data["stages"].get(stage, char_data["stages"]["初期"])["system"]
        
        # chat履歴を初期化してGeminiに送信
        convo = model.start_chat(history=[])
        convo.send_message(system_prompt)
        convo.send_message(user_text) 
        reply = convo.last.text.strip()

        # スプレッドシートへのログ書き込みとステータス更新
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
    pass 
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





