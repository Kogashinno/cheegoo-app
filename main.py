import os
import json
import datetime
import traceback
import logging 
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from characters import characters, STAGE_RULES 

app = Flask(__name__)

app.logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)

genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

app.logger.info("--- 利用可能なGeminiモデル一覧 ---")
try:
    for m in genai.list_models():
        if "generateContent" in m.supported_generation_methods:
            app.logger.info(f"利用可能モデル: {m.name}")
except Exception as e:
    app.logger.error(f"モデルリストの取得中にエラーが発生しました: {e}")
app.logger.info("--------------------------------")

# モデル名を gemini-1.5-flash-latest に変更
model = genai.GenerativeModel(model_name="models/gemini-1.5-flash-latest")


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

def write_log(sheet, data):
    try:
        sheet.append_row(data)
    except Exception as e:
        app.logger.error("ログ書き込み失敗: %s", str(e))
        traceback.print_exc()

def update_status(status_sheet, uid, char_key):
    try:
        records = status_sheet.get_all_records()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        app.logger.info(f"--- update_status開始 --- UID: '{uid}', Char: '{char_key}', Today: '{today}'")
        
        if records:
            app.logger.info(f"--- gspreadが読み込んだヘッダー（records[0]）---: {records[0].keys()}")
        else:
            app.logger.info("--- recordsが空です（シートにデータがないか、ヘッダー行のみ） ---")

        user_found = False
        for i, row in enumerate(records, start=2): 
            sheet_uid = str(row.get("uid", "")).strip() 
            
            app.logger.info(f"  シート行 {i}: uid='{sheet_uid}', 比較対象uid='{uid}'")
            app.logger.info(f"  シート行 {i} の全データ: {row}") 

            if sheet_uid == str(uid).strip(): 
                user_found = True
                app.logger.info(f"  ユーザー '{uid}' がシート行 {i} で見つかりました。")
                
                try:
                    current_gp = int(row.get("GP", 0)) 
                except ValueError:
                    app.logger.warning(f"  GP列の値 '{row.get('GP')}' が無効です。0として扱います。")
                    current_gp = 0

                last_grumble_date = row.get("最終グチ日", "")
                
                try:
                    consecutive_grumble_days = int(row.get("グチ連続日数", 0))
                except ValueError:
                    app.logger.warning(f"  グチ連続日数列の値 '{row.get('グチ連続日数')}' が無効です。0として扱います。")
                    consecutive_grumble_days = 0
                
                try:
                    total_grumble_count = int(row.get("総グチ数", 0))
                except ValueError:
                    app.logger.warning(f"  総グチ数列の値 '{row.get('総グチ数')}' が無効です。0として扱います。")
                    total_grumble_count = 0
                
                app.logger.info(f"  既存データ取得: GP={current_gp}, 最終グチ日='{last_grumble_date}', 連続日数={consecutive_grumble_days}, 総グチ数={total_grumble_count}")
                
                if last_grumble_date != today:
                    app.logger.info(f"  日付が異なります ('{last_grumble_date}' != '{today}')。GPと連続日数を加算します。")
                    current_gp += 10
                    consecutive_grumble_days += 1 
                    
                    status_sheet.update_cell(i, 7, current_gp)  # GP列 (G列)
                    status_sheet.update_cell(i, 4, today)      # 最終グチ日列 (D列)
                    status_sheet.update_cell(i, 5, consecutive_grumble_days) # グチ連続日数 (E列)
                    status_sheet.update_cell(i, 8, today)      # 最終GP付与日列 (H列)
                else:
                    app.logger.info(f"  日付が同じです ('{last_grumble_date}' == '{today}')。GPと連続日数は加算しません。")
                
                total_grumble_count += 1 
                status_sheet.update_cell(i, 6, total_grumble_count) 
                app.logger.info(f"  更新後データ: GP={current_gp}, 最終グチ日='{today}', 連続日数={consecutive_grumble_days}, 総グチ数={total_grumble_count}")
                
                return 

        if not user_found:
            app.logger.info(f"  ユーザー '{uid}' は見つかりませんでした。新規ユーザーとして追加します。")
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
            app.logger.info(f"  新規ユーザーデータ追加: {new_user_data}")
    except Exception as e:
        app.logger.error("ステータス更新エラー: %s", str(e))
        traceback.print_exc()
    app.logger.info("--- update_status終了 ---")

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

        app.logger.info(f"--- chat関数受信データ ---: {data}")
        app.logger.info(f"--- chat関数 char_key ---: '{char_key}'")

        user_text = "".join(user_text.split()) 
        
        if not user_text: 
            return jsonify({"reply": "何か入力してください。"})

        char_data = characters.get(char_key)
        if not char_data:
            return jsonify({"reply": "キャラが見つからないよ。"})

        base_system_prompt = char_data["stages"].get(stage, char_data["stages"]["初期"])["system"]
        
        control_instructions = (
            "返信には、動作の描写（例: 「私は頷きながら」「彼は微笑んで」など）を含めないでください。\n"
            "簡潔さを保ちつつも、キャラクターの個性を損なわないように、適切な長さで返信してください。"
        )
        system_prompt = f"{base_system_prompt}\n\n{control_instructions}"

        convo = model.start_chat(history=[])
        convo.send_message(system_prompt)
        
        app.logger.info(f"--- API呼び出し前 ---")
        app.logger.info(f"  システムプロンプト: {system_prompt[:50]}...")
        app.logger.info(f"  ユーザー入力: {user_text}")
        
        convo.send_message(user_text) 
        reply = convo.last.text.strip()
        
        img_path = f"static/images/{char_key}/{stage}.gif"
        
        sheet, status_sheet = get_gsheet()
        if sheet and status_sheet:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_data = [timestamp, uid, char_key, user_text, reply]
            write_log(sheet, log_data)
            update_status(status_sheet, uid, char_key)

        return jsonify({"reply": reply, "img": img_path})
    except Exception as e:
        app.logger.error("全体処理エラー: %s", str(e))
        traceback.print_exc()
        return jsonify({"reply": "エラーが発生したよ。ログを確認してね。"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

try:
    pass 
except ImportError:
    app.logger.warning("STAGE_RULES was not found in characters.py. Using a default definition in main.py.")
    STAGE_RULES = {
        "初期": {"min_gp": 0, "condition": "誰でもここから。"},
        "中期": {"min_gp": 30, "condition": "GP30以上、または3日連続グチ。"},
        "後期_陽": {"min_gp": 60, "condition": "GP60以上、かつポジティブ率50%以上。"},
        "後期_陰": {"min_gp": 60, "condition": "GP60以上、かつポジティブ率50%未満。"},
        "特別_キラキラ": {"min_gp": 100, "condition": "後期ステージ到達、かつGP100以上。"},
        "特別_固有": {"min_gp": None, "condition": "後期ステージ到達、かつキャラ別条件達成。"}
    }



