import streamlit as st
import pandas as pd
import json
import uuid
import datetime
from typing import Dict, List, Optional, Tuple
import requests
import google.generativeai as genai
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import os

# 設定
NOTION_API_VERSION = "2022-06-28"
ALLOWED_DOMAIN = "@dai.co.jp"

# 環境変数から設定を取得
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

# Gemini設定
genai.configure(api_key=GEMINI_API_KEY)

class NotionClient:
    def __init__(self, token: str, database_id: str):
        self.token = token
        self.database_id = database_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION
        }
    
    def add_faq(self, faq_data: Dict) -> bool:
        """FAQをNotionデータベースに追加"""
        url = "https://api.notion.com/v1/pages"
        
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": {
                "ID": {
                    "title": [{"text": {"content": faq_data["faq_id"]}}]
                },
                "質問": {
                    "rich_text": [{"text": {"content": faq_data["question"]}}]
                },
                "回答": {
                    "rich_text": [{"text": {"content": faq_data["answer"]}}]
                },
                "カテゴリ": {
                    "select": {"name": faq_data.get("category", "その他")}
                },
                "最終更新日": {
                    "date": {"start": datetime.datetime.now().isoformat()}
                }
            }
        }
        
        try:
            response = requests.post(url, headers=self.headers, json=payload)
            return response.status_code == 200
        except Exception as e:
            st.error(f"Notion API エラー: {str(e)}")
            return False
    
    def search_faq_by_id(self, faq_id: str) -> Optional[Dict]:
        """FAQ IDでFAQを検索"""
        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        
        payload = {
            "filter": {
                "property": "ID",
                "title": {"equals": faq_id}
            }
        }
        
        try:
            response = requests.post(url, headers=self.headers, json=payload)
            data = response.json()
            
            if data.get("results"):
                result = data["results"][0]
                properties = result["properties"]
                
                return {
                    "page_id": result["id"],
                    "faq_id": faq_id,
                    "question": properties.get("質問", {}).get("rich_text", [{}])[0].get("text", {}).get("content", ""),
                    "answer": properties.get("回答", {}).get("rich_text", [{}])[0].get("text", {}).get("content", ""),
                    "category": properties.get("カテゴリ", {}).get("select", {}).get("name", "")
                }
            return None
        except Exception as e:
            st.error(f"Notion検索エラー: {str(e)}")
            return None
    
    def get_all_faq_ids(self) -> List[str]:
        """全FAQ IDを取得"""
        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        
        all_ids = []
        has_more = True
        start_cursor = None
        
        try:
            while has_more:
                payload = {}
                if start_cursor:
                    payload["start_cursor"] = start_cursor
                    
                response = requests.post(url, headers=self.headers, json=payload)
                data = response.json()
                
                for result in data.get("results", []):
                    # IDカラムの値を取得（タイトルタイプの場合）
                    id_property = result["properties"].get("ID", {})
                    if id_property.get("title"):
                        faq_id = id_property["title"][0].get("text", {}).get("content", "")
                    elif id_property.get("rich_text"):
                        faq_id = id_property["rich_text"][0].get("text", {}).get("content", "")
                    else:
                        faq_id = ""
                        
                    if faq_id and faq_id.isdigit():
                        all_ids.append(faq_id)
                
                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")
                
        except Exception as e:
            st.error(f"FAQ ID取得エラー: {str(e)}")
        
        return all_ids
    
    def update_faq(self, page_id: str, faq_data: Dict) -> bool:
        """既存FAQを更新"""
        url = f"https://api.notion.com/v1/pages/{page_id}"
        
        payload = {
            "properties": {
                "質問": {
                    "rich_text": [{"text": {"content": faq_data["question"]}}]
                },
                "回答": {
                    "rich_text": [{"text": {"content": faq_data["answer"]}}]
                },
                "カテゴリ": {
                    "select": {"name": faq_data.get("category", "その他")}
                },
                "最終更新日": {
                    "date": {"start": datetime.datetime.now().isoformat()}
                }
            }
        }
        
        try:
            response = requests.patch(url, headers=self.headers, json=payload)
            return response.status_code == 200
        except Exception as e:
            st.error(f"Notion更新エラー: {str(e)}")
            return False

class AIAssistant:
    @staticmethod
    def get_faq_suggestions(update_content: str, current_faq: Dict) -> Dict:
        """Gemini 2.5 ProでAI修正候補を生成"""
        prompt = f"""
        以下のアップデート内容を踏まえて、既存のFAQを修正する必要があるか判定し、必要であれば修正案を提案してください。

        【アップデート内容】
        {update_content}

        【既存FAQ】
        質問: {current_faq['question']}
        回答: {current_faq['answer']}

        以下のJSON形式で回答してください：
        {{
            "needs_update": true/false,
            "reason": "修正が必要な理由",
            "suggested_question": "修正後の質問（変更不要なら元のまま）",
            "suggested_answer": "修正後の回答（変更不要なら元のまま）"
        }}
        
        JSONのみを返してください。他の説明は不要です。
        """
        
        try:
            model = genai.GenerativeModel('gemini-2.5-pro')
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=1000,
                    response_mime_type="application/json"
                )
            )
            
            content = response.text
            return json.loads(content)
        except Exception as e:
            st.error(f"Gemini API エラー: {str(e)}")
            return {
                "needs_update": False,
                "reason": "AI処理でエラーが発生しました",
                "suggested_question": current_faq['question'],
                "suggested_answer": current_faq['answer']
            }

def verify_google_token(token: str) -> Optional[Dict]:
    """Google OAuthトークンを検証"""
    try:
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)
        
        # ドメイン制限チェック
        email = idinfo.get('email', '')
        if not email.endswith(ALLOWED_DOMAIN):
            return None
            
        return {
            'email': email,
            'name': idinfo.get('name', ''),
            'picture': idinfo.get('picture', '')
        }
    except Exception:
        return None

def get_next_faq_id(notion_client: NotionClient) -> str:
    """既存FAQの最大番号+1を取得"""
    try:
        all_ids = notion_client.get_all_faq_ids()
        
        if not all_ids:
            return "0001"  # 最初のFAQ
        
        # 数値部分を抽出して最大値取得
        max_num = max([int(faq_id) for faq_id in all_ids if faq_id.isdigit() and len(faq_id) == 4])
        next_num = max_num + 1
        
        return f"{next_num:04d}"  # 4桁フォーマット
        
    except Exception as e:
        st.error(f"次のFAQ ID取得エラー: {str(e)}")
        return "0001"  # エラー時は0001から

def generate_faq_id() -> str:
    """FAQ IDを生成"""
    return f"FAQ_{str(uuid.uuid4())[:8].upper()}"

def main():
    st.set_page_config(
        page_title="FAQ管理アプリ 📋",
        page_icon="📋",
        layout="wide"
    )
    
    st.title("📋 FAQ管理アプリ")
    st.markdown("---")
    
    # セッション状態の初期化
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'user_info' not in st.session_state:
        st.session_state.user_info = {}
    
    # 認証チェック
    if not st.session_state.authenticated:
        st.subheader("🔐 ログインが必要です")
        st.info(f"このアプリを使用するには、{ALLOWED_DOMAIN}のGoogleアカウントでログインしてください。")
        
        # Google OAuthのダミー実装（実際は適切なOAuthライブラリを使用）
        with st.form("login_form"):
            st.markdown("### Google OAuthログイン（開発用）")
            email = st.text_input("メールアドレス", placeholder="your-name@dai.co.jp")
            name = st.text_input("お名前", placeholder="山田太郎")
            
            if st.form_submit_button("ログイン"):
                if email.endswith(ALLOWED_DOMAIN):
                    st.session_state.authenticated = True
                    st.session_state.user_info = {"email": email, "name": name}
                    st.success("✅ ログインしました！")
                    st.rerun()
                else:
                    st.error(f"❌ {ALLOWED_DOMAIN}のドメインのみ利用可能です")
        return
    
    # メインアプリケーション
    st.sidebar.success(f"👤 {st.session_state.user_info['name']} さん")
    st.sidebar.info(f"📧 {st.session_state.user_info['email']}")
    
    if st.sidebar.button("🚪 ログアウト"):
        st.session_state.authenticated = False
        st.session_state.user_info = {}
        st.rerun()
    
    # API設定チェック
    if not all([NOTION_TOKEN, NOTION_DATABASE_ID, GEMINI_API_KEY]):
        st.error("⚠️ 環境変数が不足しています。NOTION_TOKEN, NOTION_DATABASE_ID, GEMINI_API_KEYを設定してください。")
        return
    
    # Notionクライアント初期化
    notion_client = NotionClient(NOTION_TOKEN, NOTION_DATABASE_ID)
    
    # モード選択
    st.subheader("🔘 操作モードを選択")
    mode = st.radio(
        "モードを選択してください",
        ["➕ FAQ追加", "🛠 FAQ更新"],
        horizontal=True
    )
    
    st.markdown("---")
    
    if mode == "➕ FAQ追加":
        st.subheader("➕ FAQ追加モード")
        
        # 入力方法選択
        input_method = st.radio(
            "入力方法を選択",
            ["📝 1件ずつ入力", "📊 CSV一括取り込み"],
            horizontal=True
        )
        
        if input_method == "📝 1件ずつ入力":
            with st.form("add_faq_form"):
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    question = st.text_area("📝 質問", placeholder="ユーザーからの問い合わせ想定文を入力...")
                    answer = st.text_area("💬 回答", placeholder="それに対する回答内容を入力...")
                
                with col2:
                    category = st.selectbox(
                        "🏷 カテゴリ",
                        ["ログイン", "支払い", "機能", "トラブル", "その他"],
                        index=4
                    )
                    
                    # 次のFAQ IDを自動取得
                    next_id = get_next_faq_id(notion_client)
                    faq_id = st.text_input("🆔 FAQ ID", value=next_id, disabled=True)
                    st.info(f"💡 自動で次の番号({next_id})が設定されました")
                
                if st.form_submit_button("💾 FAQを追加", type="primary"):
                    if question and answer:
                        faq_data = {
                            "faq_id": faq_id,
                            "question": question,
                            "answer": answer,
                            "category": category
                        }
                        
                        with st.spinner("Notionに追加中... ⏳"):
                            if notion_client.add_faq(faq_data):
                                st.success("✅ FAQが正常に追加されました！")
                                st.info(f"FAQ ID: `{faq_id}`")
                            else:
                                st.error("❌ FAQ追加に失敗しました")
                    else:
                        st.error("❌ 質問と回答は必須です")
        
        else:  # CSV一括取り込み
            st.markdown("### 📊 CSV一括取り込み")
            
            uploaded_file = st.file_uploader(
                "CSVファイルを選択",
                type=['csv'],
                help="カラム: question, answer, category"
            )
            
            if uploaded_file:
                try:
                    df = pd.read_csv(uploaded_file)
                    st.dataframe(df)
                    
                    if st.button("📥 一括追加実行", type="primary"):
                        success_count = 0
                        
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        for idx, row in df.iterrows():
                            # 次のFAQ IDを取得
                            next_id = get_next_faq_id(notion_client)
                            
                            faq_data = {
                                "faq_id": next_id,
                                "question": str(row.get('question', '')),
                                "answer": str(row.get('answer', '')),
                                "category": str(row.get('category', 'その他'))
                            }
                            
                            status_text.text(f"処理中... {idx + 1}/{len(df)}")
                            
                            if notion_client.add_faq(faq_data):
                                success_count += 1
                            
                            progress_bar.progress((idx + 1) / len(df))
                        
                        st.success(f"✅ {success_count}/{len(df)} 件のFAQを追加しました！")
                        
                except Exception as e:
                    st.error(f"❌ CSV読み込みエラー: {str(e)}")
    
    elif mode == "🛠 FAQ更新":
        st.subheader("🛠 FAQ更新モード")
        
        # FAQ検索
        search_id = st.text_input("🔍 FAQ IDで検索", placeholder="FAQ_XXXXXXXX")
        
        if st.button("🔍 検索実行"):
            if search_id:
                with st.spinner("検索中... ⏳"):
                    faq_data = notion_client.search_faq_by_id(search_id)
                    
                    if faq_data:
                        st.session_state.current_faq = faq_data
                        st.success("✅ FAQが見つかりました！")
                    else:
                        st.error("❌ 指定されたFAQ IDが見つかりません")
            else:
                st.warning("⚠️ FAQ IDを入力してください")
        
        # FAQ更新フォーム
        if 'current_faq' in st.session_state:
            current_faq = st.session_state.current_faq
            
            st.markdown("### 📋 現在のFAQ内容")
            with st.expander("詳細を表示", expanded=True):
                st.write(f"**FAQ ID:** `{current_faq['faq_id']}`")
                st.write(f"**カテゴリ:** {current_faq['category']}")
                st.write(f"**質問:** {current_faq['question']}")
                st.write(f"**回答:** {current_faq['answer']}")
            
            # 更新方法選択
            update_method = st.radio(
                "更新方法を選択",
                ["📌 手動修正", "💎 Gemini修正候補を利用"],
                horizontal=True
            )
            
            if update_method == "📌 手動修正":
                with st.form("manual_update_form"):
                    col1, col2 = st.columns([2, 1])
                    
                    with col1:
                        new_question = st.text_area("📝 質問", value=current_faq['question'])
                        new_answer = st.text_area("💬 回答", value=current_faq['answer'])
                    
                    with col2:
                        new_category = st.selectbox(
                            "🏷 カテゴリ",
                            ["ログイン", "支払い", "機能", "トラブル", "その他"],
                            index=["ログイン", "支払い", "機能", "トラブル", "その他"].index(current_faq.get('category', 'その他')) if current_faq.get('category', 'その他') in ["ログイン", "支払い", "機能", "トラブル", "その他"] else 4
                        )
                    
                    if st.form_submit_button("💾 更新実行", type="primary"):
                        update_data = {
                            "question": new_question,
                            "answer": new_answer,
                            "category": new_category
                        }
                        
                        with st.spinner("更新中... ⏳"):
                            if notion_client.update_faq(current_faq['page_id'], update_data):
                                st.success("✅ FAQが正常に更新されました！")
                                # セッションステートをクリア
                                del st.session_state.current_faq
                            else:
                                st.error("❌ FAQ更新に失敗しました")
            
            else:  # Gemini修正候補
                st.markdown("### 💎 Gemini修正候補")
                
                update_content = st.text_area(
                    "📄 アップデート内容",
                    placeholder="リリースノートや変更内容をべた貼りしてください...",
                    height=150
                )
                
                if st.button("💎 Gemini修正候補を生成", type="primary"):
                    if update_content:
                        with st.spinner("Gemini分析中... 🧠"):
                            suggestions = AIAssistant.get_faq_suggestions(update_content, current_faq)
                        
                        if suggestions['needs_update']:
                            st.success("✅ 修正が推奨されます")
                            st.write(f"**理由:** {suggestions['reason']}")
                            
                            with st.form("ai_update_form"):
                                st.markdown("#### 🔧 修正案")
                                
                                col1, col2 = st.columns([2, 1])
                                
                                with col1:
                                    suggested_question = st.text_area(
                                        "📝 修正後の質問",
                                        value=suggestions['suggested_question']
                                    )
                                    suggested_answer = st.text_area(
                                        "💬 修正後の回答",
                                        value=suggestions['suggested_answer']
                                    )
                                
                                with col2:
                                    new_category = st.selectbox(
                                        "🏷 カテゴリ",
                                        ["ログイン", "支払い", "機能", "トラブル", "その他"],
                                        index=["ログイン", "支払い", "機能", "トラブル", "その他"].index(current_faq.get('category', 'その他')) if current_faq.get('category', 'その他') in ["ログイン", "支払い", "機能", "トラブル", "その他"] else 4
                                    )
                                
                                if st.form_submit_button("💎 Gemini修正案で更新", type="primary"):
                                    update_data = {
                                        "question": suggested_question,
                                        "answer": suggested_answer,
                                        "category": new_category
                                    }
                                    
                                    with st.spinner("更新中... ⏳"):
                                        if notion_client.update_faq(current_faq['page_id'], update_data):
                                            st.success("✅ FAQがGemini修正案で更新されました！")
                                            # セッションステートをクリア
                                            del st.session_state.current_faq
                                        else:
                                            st.error("❌ FAQ更新に失敗しました")
                        
                        else:
                            st.info("ℹ️ 現在のFAQに修正は不要です")
                            st.write(f"**理由:** {suggestions['reason']}")
                    
                    else:
                        st.warning("⚠️ アップデート内容を入力してください")
    
    # フッター
    st.markdown("---")
    st.markdown(
        """
        <div style='text-align: center; color: #666; font-size: 0.8em;'>
            📋 FAQ管理アプリ | Streamlit + Notion連携 | Made with ❤️
        </div>
        """,
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()
