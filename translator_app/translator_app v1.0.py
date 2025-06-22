import sys
import random
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                           QHBoxLayout, QTextEdit, QComboBox, QPushButton, 
                           QLabel, QGridLayout, QLineEdit, QMessageBox)
from PyQt6.QtCore import Qt, QUrl, QSettings, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from deep_translator import GoogleTranslator, MicrosoftTranslator
from google.cloud import translate_v2 as google_translate
from groq import Groq
import translators as ts
import deepl
import edge_tts
import pyttsx3
import google.generativeai as genai
from gtts import gTTS
import asyncio
import os
import tempfile
import json
import atexit

class TranslatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("雙向翻譯器")
        self.setGeometry(100, 100, 1200, 800)
        
        # 初始化翻譯方向（True表示從左到右翻譯，False表示從右到左）
        self.translate_left_to_right = True
        self.last_source_text = "" # 用於儲存最近一次的原文
        
        # 初始化臨時文件列表和清理計時器
        self.temp_files = []
        self.cleanup_timer = QTimer()
        self.cleanup_timer.timeout.connect(self.cleanup_temp_files)
        self.cleanup_timer.start(60000)  # 每分鐘檢查一次
        
        # 初始化翻譯延遲計時器
        self.translate_timer = QTimer()
        self.translate_timer.setSingleShot(True)  # 設置為單次觸發
        self.translate_timer.timeout.connect(self.translate_text)
        
        # 註冊程序退出時的清理函數
        atexit.register(self.cleanup_all_temp_files)
        
        # 加載設置
        self.settings = QSettings('TranslatorApp', 'Settings')
        self.load_settings()
        
        # 初始化媒體播放器
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.current_playing_file = None
        
        # 設置播放狀態變化的處理
        self.player.mediaStatusChanged.connect(self.handle_media_status_changed)
        
        # 初始化 pyttsx3
        self.tts_engine = pyttsx3.init()
        
        # 創建主窗口部件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        # 創建主布局
        layout = QGridLayout()
        main_widget.setLayout(layout)
        
        # 創建主設置區域
        settings_widget = QWidget()
        settings_layout = QVBoxLayout()
        settings_widget.setLayout(settings_layout)
        
        # 翻譯服務設置
        translate_settings = QHBoxLayout()
        
        # 翻譯服務選擇
        self.translator_combo = QComboBox()
        self.translator_combo.addItems([
            "Google翻譯 (免費)",
            "Groq Llama3 (免費)",
            "Gemini Pro API (付費)",
            "Google Cloud Translation API",
            "Microsoft Translator API",
            "DeepL API",
            "Translators 聚合翻譯 (免費)"
        ])
        translate_settings.addWidget(QLabel("翻譯服務:"))
        translate_settings.addWidget(self.translator_combo)
        
        # API Key 輸入
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("請輸入 API Key")
        self.api_key_input.setText(self.api_keys.get(self.translator_combo.currentText(), ""))
        translate_settings.addWidget(QLabel("API Key:"))
        translate_settings.addWidget(self.api_key_input)
        
        # 保存API Key按鈕
        save_api_btn = QPushButton("保存API Key")
        save_api_btn.clicked.connect(self.save_api_key)
        translate_settings.addWidget(save_api_btn)
        
        # 語音服務設置
        voice_settings = QHBoxLayout()
        
        # 語音服務選擇
        self.voice_combo = QComboBox()
        self.voice_combo.addItems([
            "Edge TTS",
            "Google TTS",
            "System TTS (pyttsx3)"
        ])
        voice_settings.addWidget(QLabel("語音服務:"))
        voice_settings.addWidget(self.voice_combo)
        
        # 添加設置到布局
        settings_layout.addLayout(translate_settings)
        settings_layout.addLayout(voice_settings)
        
        # 左側翻譯區域
        left_widget = QWidget()
        left_layout = QVBoxLayout()
        left_widget.setLayout(left_layout)
        
        # 左側語言選擇
        self.left_lang_combo = QComboBox()
        self.left_lang_combo.addItems(["中文", "英文", "日文"])
        left_layout.addWidget(QLabel("來源語言:"))
        left_layout.addWidget(self.left_lang_combo)
        
        # 左側文本輸入
        self.left_text = QTextEdit()
        left_layout.addWidget(QLabel("輸入文本:"))
        left_layout.addWidget(self.left_text)
        # 連接文本變化信號
        self.left_text.textChanged.connect(lambda: self.handle_text_changed(True))
        
        # 左側語音按鈕
        self.left_speak_btn = QPushButton("播放語音")
        self.left_speak_btn.clicked.connect(lambda: self.handle_speak_button(self.left_text.toPlainText(), self.get_lang_code(self.left_lang_combo.currentText())))
        left_layout.addWidget(self.left_speak_btn)
        
        # 右側翻譯區域
        right_widget = QWidget()
        right_layout = QVBoxLayout()
        right_widget.setLayout(right_layout)
        
        # 右側語言選擇
        self.right_lang_combo = QComboBox()
        self.right_lang_combo.addItems(["中文", "英文", "日文"])
        right_layout.addWidget(QLabel("目標語言:"))
        right_layout.addWidget(self.right_lang_combo)
        
        # 右側文本顯示
        self.right_text = QTextEdit()
        self.right_text.setReadOnly(False)  # 允許編輯
        right_layout.addWidget(QLabel("翻譯結果:"))
        right_layout.addWidget(self.right_text)
        # 連接右側文本變化信號
        self.right_text.textChanged.connect(lambda: self.handle_text_changed(False))
        
        # 右側語音按鈕
        self.right_speak_btn = QPushButton("播放語音")
        self.right_speak_btn.clicked.connect(lambda: self.handle_speak_button(self.right_text.toPlainText(), self.get_lang_code(self.right_lang_combo.currentText())))
        right_layout.addWidget(self.right_speak_btn)
        
        # --- 以下為新增的 UI 元素 ---
        self.correction_input = QLineEdit()
        self.correction_input.setPlaceholderText("如果您對翻譯不滿意，請在此輸入您的版本")
        right_layout.addWidget(QLabel("提供更好的翻譯 (可選):"))
        right_layout.addWidget(self.correction_input)

        self.learn_button = QPushButton("學習此翻譯")
        self.learn_button.clicked.connect(self.save_correction)
        right_layout.addWidget(self.learn_button)
        # --- UI 元素新增完畢 ---
        
        # 翻譯按鈕
        translate_btn = QPushButton("翻譯")
        translate_btn.clicked.connect(self.handle_translate_button)
        
        # 交換語言按鈕
        swap_btn = QPushButton("交換語言")
        swap_btn.clicked.connect(self.handle_swap_button)
        
        # 添加所有部件到主布局
        layout.addWidget(settings_widget, 0, 0, 1, 2)
        layout.addWidget(left_widget, 1, 0)
        layout.addWidget(right_widget, 1, 1)
        layout.addWidget(translate_btn, 2, 0)
        layout.addWidget(swap_btn, 2, 1)
        
        # 連接信號
        self.translator_combo.currentTextChanged.connect(self.handle_service_changed)
        self.left_lang_combo.currentTextChanged.connect(self.handle_language_changed)
        self.right_lang_combo.currentTextChanged.connect(self.handle_language_changed)
        
        # 初始化輸入框的唯讀狀態
        self.left_text.setReadOnly(False)
        self.right_text.setReadOnly(True)
        
    def load_settings(self):
        """加載所有設置"""
        self.api_keys = json.loads(self.settings.value('api_keys', '{}'))
        
    def save_api_key(self):
        """保存當前服務的API Key"""
        self.stop_audio()
        service = self.translator_combo.currentText()
        api_key = self.api_key_input.text().strip()
        self.api_keys[service] = api_key
        self.settings.setValue('api_keys', json.dumps(self.api_keys))
        QMessageBox.information(self, "成功", f"{service} 的 API Key 已保存")
        
    def save_correction(self):
        """保存使用者提供的更佳翻譯範例"""
        source_text = self.last_source_text.strip()
        corrected_translation = self.correction_input.text().strip()

        if not source_text or not corrected_translation:
            QMessageBox.warning(self, "錯誤", "必須有原文和您修正後的譯文才能學習！")
            return

        # 從設定中讀取已有的範例
        examples_json = self.settings.value('translation_examples', '[]')
        examples = json.loads(examples_json)

        # 新增範例 (避免重複)
        new_example = {"source": source_text, "translation": corrected_translation}
        if new_example not in examples:
            examples.append(new_example)

        # 為了避免 prompt 過長，只保留最新的 10 個範例
        if len(examples) > 10:
            examples = examples[-10:]

        # 將更新後的範例列表存回設定
        self.settings.setValue('translation_examples', json.dumps(examples, ensure_ascii=False))
        self.correction_input.clear() # 清空輸入框
        QMessageBox.information(self, "成功", "學習成功！這個範例將在未來的翻譯中作為參考。")

    def handle_service_changed(self, service):
        """處理翻譯服務變更"""
        self.stop_audio()
        self.update_api_key_input(service)
        self.trigger_translation()

    def handle_language_changed(self):
        """處理語言選擇變更"""
        self.stop_audio()
        self.trigger_translation()

    def update_api_key_input(self, service):
        """更新API Key輸入框的內容"""
        self.api_key_input.setText(self.api_keys.get(service, ""))

    def trigger_translation(self):
        """觸發翻譯"""
        # 使用 QTimer 延遲觸發，以避免在快速切換時過度請求
        self.translate_timer.start(50) 
            
    def handle_text_changed(self, is_left_side):
        """處理文本變化"""
        if is_left_side != self.translate_left_to_right:
            return
            
        source_text = self.left_text.toPlainText() if self.translate_left_to_right else self.right_text.toPlainText()
        
        if not source_text.strip():
            target_widget = self.right_text if self.translate_left_to_right else self.left_text
            target_widget.clear()
            return
            
        self.translate_timer.start(1200)
        
    def translate_text(self):
        """執行翻譯"""
        translation = ""
        if self.translate_left_to_right:
            source_text = self.left_text.toPlainText().strip()
            src_lang = self.get_lang_code(self.left_lang_combo.currentText())
            dest_lang = self.get_lang_code(self.right_lang_combo.currentText())
            target_widget = self.right_text
        else:
            source_text = self.right_text.toPlainText().strip()
            src_lang = self.get_lang_code(self.right_lang_combo.currentText())
            dest_lang = self.get_lang_code(self.left_lang_combo.currentText())
            target_widget = self.left_text
            
        if not source_text:
            target_widget.clear()
            return

        # 儲存當前原文，以供學習功能使用
        self.last_source_text = source_text
            
        service = self.translator_combo.currentText()
        
        try:
            if service == "Google翻譯 (免費)":
                translator = GoogleTranslator(source=src_lang["google"], target=dest_lang["google"])
                translation = translator.translate(text=source_text)

            elif service == "Groq Llama3 (免費)":
                if not self.api_keys.get(service):
                    target_widget.setText("請先設置 Groq API Key")
                    return
                
                try:
                    # 在 try 區塊的開頭，先 import opencc
                    import opencc
                    
                    client = Groq(api_key=self.api_keys.get(service))
                    
                    # --- BUG 修正: 動態獲取來源和目標語言名稱 ---
                    source_lang_name = self.left_lang_combo.currentText() if self.translate_left_to_right else self.right_lang_combo.currentText()
                    target_lang_name = self.right_lang_combo.currentText() if self.translate_left_to_right else self.left_lang_combo.currentText()
                    
                    # --- 步驟 A: 生成草稿 ---
                    draft_prompt = (
                        f"You are a helpful bilingual assistant. Your task is to provide a direct and solid translation of the "
                        f"following {source_lang_name} text into {target_lang_name}. "
                        f"Provide only the translated text.\n\n"
                        f"Text to translate: \"{source_text}\""
                    )
                    draft_completion = client.chat.completions.create(
                        messages=[{"role": "user", "content": draft_prompt}],
                        model="llama3-8b-8192",
                        temperature=0.2
                    )
                    draft_translation = draft_completion.choices[0].message.content.strip()

                    # --- 準備「少樣本」範例 ---
                    examples_json = self.settings.value('translation_examples', '[]')
                    examples = json.loads(examples_json)
                    
                    examples_str = ""
                    if examples:
                        selected_examples = random.sample(examples, min(len(examples), 3))
                        examples_str += "Here are some examples of my preferred final translation style. Use them as a style guide:\n"
                        for ex in selected_examples:
                            examples_str += f"- Source: \"{ex['source']}\"\n  Preferred Translation: \"{ex['translation']}\"\n"
                        examples_str += "\n"

                    # --- 步驟 B: 專家審核與優化 ---
                    # 根據目標語言，動態設定 prompt 的繁體中文要求
                    tc_instruction = ""
                    if target_lang_name == "中文":
                        tc_instruction = "Your response MUST BE in Traditional Chinese (繁體中文) only. Do not use Simplified Chinese. Ensure the final output is perfectly idiomatic for a native speaker from Taiwan."
                    
                    refinement_prompt = (
                        f"You are a world-class editor and localization expert for translating from {source_lang_name} to {target_lang_name}.\n"
                        f"Your task is to critically review and refine an initial translation based on my preferred style. "
                        f"Correct any unnatural phrasing, literal translations, or errors. \n"
                        f"{tc_instruction}\n\n" # 插入繁體中文的特別指令
                        f"{examples_str}" # 插入您的風格指南
                        f"--- Task ---\n"
                        f"Original {source_lang_name} Text: \"{source_text}\"\n"
                        f"Initial Draft Translation: \"{draft_translation}\"\n\n"
                        f"Provide ONLY the final, improved translation in {target_lang_name}:"
                    )
                    final_completion = client.chat.completions.create(
                        messages=[{"role": "user", "content": refinement_prompt}],
                        model="llama3-70b-8192",
                        temperature=0.3
                    )
                    translation = final_completion.choices[0].message.content.strip()

                    # --- NEW: 強制進行簡轉繁 (最終保險) ---
                    if target_lang_name == "中文":
                        cc = opencc.OpenCC('s2twp')  # s2twp 表示「簡體到繁體台灣用語」
                        translation = cc.convert(translation)
                    
                    # 移除可能出現的引號
                    if translation.startswith('"') and translation.endswith('"'):
                        translation = translation[1:-1]

                except Exception as e:
                    translation = f"Groq API 錯誤: {str(e)}"
            
            # ... 其他翻譯服務的 elif 區塊維持不變 ...
            elif service == "Gemini Pro API (付費)":
                if not self.api_keys.get(service):
                    target_widget.setText("請先保存 Gemini Pro API Key")
                    return
                genai.configure(api_key=self.api_keys[service])
                model = genai.GenerativeModel('gemini-1.5-pro-latest')
                safety_settings = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ]
                source_lang_name = self.left_lang_combo.currentText() if self.translate_left_to_right else self.right_lang_combo.currentText()
                target_lang_name = self.right_lang_combo.currentText() if self.translate_left_to_right else self.left_lang_combo.currentText()
                prompt = f"請將以下「{source_lang_name}」文本翻譯成「{target_lang_name}」，只需要提供翻譯後的文本即可，不要包含任何額外的解釋或標籤：\n\n{source_text}"
                response = model.generate_content(prompt, safety_settings=safety_settings)
                translation = response.text

            elif service == "Google Cloud Translation API":
                # 此處的 google.cloud.translate.Client 初始化可能需要更完整的認證方式，例如服務帳戶
                # 這裡僅為示意，實際使用可能需要配置 GOOGLE_APPLICATION_CREDENTIALS 環境變數
                if not self.api_keys.get(service):
                    target_widget.setText("請先設置 Google Cloud API Key (或設定憑證)")
                    return
                # client = google_translate.Client.from_service_account_json(self.api_keys[service]) 
                # 或其他認證方式
                target_widget.setText("Google Cloud API 需要服務帳戶憑證，暫未實現")
                return


            elif service == "Microsoft Translator API":
                if not self.api_keys.get(service):
                    target_widget.setText("請先設置 Microsoft Translator API Key")
                    return
                translator = MicrosoftTranslator(
                    api_key=self.api_keys[service],
                    source=src_lang["microsoft"],
                    target=dest_lang["microsoft"]
                )
                translation = translator.translate(text=source_text)

            elif service == "DeepL API":
                if not self.api_keys.get(service):
                    target_widget.setText("請先設置 DeepL API Key")
                    return
                translator = deepl.Translator(self.api_keys[service])
                result = translator.translate_text(
                    source_text,
                    source_lang=src_lang["deepl"],
                    target_lang=dest_lang["deepl"]
                )
                translation = result.text

            elif service == "Translators 聚合翻譯 (免費)":
                # 轉換語言代碼
                translators_lang_map = {
                    "zh-TW": "zh",
                    "zh-CN": "zh",
                    "en": "en",
                    "ja": "ja"
                }
                src_code = translators_lang_map.get(src_lang["google"], "en")
                dest_code = translators_lang_map.get(dest_lang["google"], "zh")

                # --- 修正開始 ---
                # 1. 將翻譯引擎從 'youdao' 更換為支援更廣的 'google'
                translation = ts.translate_text(
                    query_text=source_text,
                    from_language=src_code,
                    to_language=dest_code,
                    translator='bing'  # 或者 'bing' 'youdao' 'google'
                )

                # 2. 如果目標語言是中文，則強制進行簡轉繁
                if dest_code == 'zh':
                    import opencc
                    # s2twp 表示「簡體到繁體台灣用語」，能更好地處理兩岸用語差異
                    cc = opencc.OpenCC('s2twp') 
                    translation = cc.convert(translation)
                # --- 修正結束 ---

            if translation:
                target_widget.setText(translation)
            else:
                # 避免在API呼叫成功但返回空字串時，顯示"翻譯錯誤"
                # 只有在 translation 變數從未被賦值時，才可能觸發 UnboundLocalError
                # 但我們已在函數開頭初始化，所以此路徑理論上不會顯示這個
                if 'translation' in locals() and translation == "":
                    pass # 如果翻譯結果為空字串，則顯示空字串
                else:
                    target_widget.setText("翻譯錯誤或無結果")

        except Exception as e:
            target_widget.setText(f"翻譯錯誤: {str(e)}")
    
    def swap_languages(self):
        """交換語言和文本"""
        self.stop_audio()
        
        # 交換語言選擇
        left_index = self.left_lang_combo.currentIndex()
        right_index = self.right_lang_combo.currentIndex()
        self.left_lang_combo.setCurrentIndex(right_index)
        self.right_lang_combo.setCurrentIndex(left_index)
        
        # 暫時禁用文本變化信號
        self.left_text.textChanged.disconnect()
        self.right_text.textChanged.disconnect()
        
        # 交換文本
        left_text_content = self.left_text.toPlainText()
        right_text_content = self.right_text.toPlainText()
        self.left_text.setPlainText(right_text_content)
        self.right_text.setPlainText(left_text_content)
        
        # 切換翻譯方向
        self.translate_left_to_right = not self.translate_left_to_right
        
        # 更新唯讀狀態和樣式
        self.update_editor_states()
        
        # 重新連接文本變化信號
        self.left_text.textChanged.connect(lambda: self.handle_text_changed(True))
        self.right_text.textChanged.connect(lambda: self.handle_text_changed(False))
    
    def update_editor_states(self):
        """根據翻譯方向更新編輯器的狀態和樣式"""
        if self.translate_left_to_right:
            self.left_text.setReadOnly(False)
            self.right_text.setReadOnly(True)
            self.left_text.setStyleSheet("background-color: white;")
            self.right_text.setStyleSheet("background-color: #f0f0f0;")
        else:
            self.left_text.setReadOnly(True)
            self.right_text.setReadOnly(False)
            self.left_text.setStyleSheet("background-color: #f0f0f0;")
            self.right_text.setStyleSheet("background-color: white;")

    def handle_media_status_changed(self, status):
        """處理媒體播放狀態變化"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.current_playing_file:
                try:
                    # 檔案可能已被清理，所以先檢查是否存在
                    if os.path.exists(self.current_playing_file):
                        os.unlink(self.current_playing_file)
                    if self.current_playing_file in self.temp_files:
                        self.temp_files.remove(self.current_playing_file)
                except (PermissionError, OSError) as e:
                    print(f"清理臨時文件時出錯: {e}")
                finally:
                    self.current_playing_file = None
    
    def cleanup_temp_files(self):
        """清理不再使用的臨時文件"""
        for temp_file in self.temp_files[:]:
            if temp_file != self.current_playing_file:
                try:
                    if os.path.exists(temp_file):
                        os.unlink(temp_file)
                    self.temp_files.remove(temp_file)
                except (PermissionError, OSError) as e:
                    print(f"定期清理時出錯: {e}")
                    continue
    
    def cleanup_all_temp_files(self):
        """程序退出時清理所有臨時文件"""
        self.stop_audio()
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except (PermissionError, OSError) as e:
                print(f"程序退出清理時出錯: {e}")
                continue
    
    async def text_to_speech(self, text, language_codes):
        if not text:
            return
            
        service = self.voice_combo.currentText()
        temp_file = None
        
        try:
            if service == "Edge TTS":
                voice_map = {
                    "zh-TW": "zh-TW-HsiaoChenNeural",
                    "en": "en-US-JennyNeural",
                    "ja": "ja-JP-NanamiNeural"
                }
                voice = voice_map.get(language_codes["google"], "en-US-JennyNeural")
                communicate = edge_tts.Communicate(text, voice)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tf:
                    temp_file = tf.name
                await communicate.save(temp_file)
                
            elif service == "Google TTS":
                tts = gTTS(text=text, lang=language_codes["google"])
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tf:
                    temp_file = tf.name
                tts.save(temp_file)
                
            elif service == "System TTS (pyttsx3)":
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tf:
                    temp_file = tf.name
                self.tts_engine.save_to_file(text, temp_file)
                self.tts_engine.runAndWait()
            
            if temp_file:
                self.temp_files.append(temp_file)
            return temp_file
            
        except Exception as e:
            print(f"TTS 錯誤: {e}")
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except (PermissionError, OSError):
                    pass
            return None
    
    def speak_text(self, text, language_codes):
        if not text:
            return
        
        # 啟動異步任務
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.text_to_speech(text, language_codes), loop)
            temp_file = future.result()
        else:
            temp_file = loop.run_until_complete(self.text_to_speech(text, language_codes))
            
        if temp_file:
            try:
                self.current_playing_file = temp_file
                self.player.setSource(QUrl.fromLocalFile(temp_file))
                self.audio_output.setVolume(50)
                self.player.play()
            except Exception as e:
                QMessageBox.warning(self, "錯誤", f"播放音頻時發生錯誤: {str(e)}")
                # 立即清理播放失敗的文件
                if self.current_playing_file:
                    if os.path.exists(self.current_playing_file):
                        os.unlink(self.current_playing_file)
                    if self.current_playing_file in self.temp_files:
                        self.temp_files.remove(self.current_playing_file)
                    self.current_playing_file = None
        else:
            QMessageBox.warning(self, "錯誤", "無法生成語音文件。")

    def stop_audio(self):
        """停止當前音頻播放並清理文件"""
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.stop()
            # 停止時立即清理關聯的臨時文件
            if self.current_playing_file:
                # 給一點時間讓文件鎖定解除
                QTimer.singleShot(100, lambda: self.cleanup_single_file(self.current_playing_file))
                self.current_playing_file = None

    def cleanup_single_file(self, file_path):
        """安全地清理單一文件"""
        if file_path and os.path.exists(file_path):
            try:
                os.unlink(file_path)
                if file_path in self.temp_files:
                    self.temp_files.remove(file_path)
            except (PermissionError, OSError) as e:
                print(f"清理文件 {file_path} 時出錯: {e}")

    def handle_speak_button(self, text, language):
        """處理語音按鈕點擊"""
        self.stop_audio()
        self.speak_text(text, language)

    def handle_translate_button(self):
        """處理翻譯按鈕點擊"""
        self.stop_audio()
        self.translate_text()

    def handle_swap_button(self):
        """處理交換語言按鈕點擊"""
        self.stop_audio()
        self.swap_languages()

    def closeEvent(self, event):
        """窗口關閉時的處理"""
        self.cleanup_all_temp_files()
        self.settings.sync() # 確保所有設置都寫入磁碟
        super().closeEvent(event)

    def get_lang_code(self, language):
        lang_dict = {
            "中文": {
                "google": "zh-TW", "microsoft": "zh-Hant", "deepl": "ZH"
            },
            "英文": {
                "google": "en", "microsoft": "en", "deepl": "EN-US"
            },
            "日文": {
                "google": "ja", "microsoft": "ja", "deepl": "JA"
            }
        }
        return lang_dict.get(language, {"google": "en", "microsoft": "en", "deepl": "EN-US"})

if __name__ == '__main__':
    # 設置異步事件循環策略（適用於 Windows）
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    app = QApplication(sys.argv)
    window = TranslatorApp()
    window.show()
    sys.exit(app.exec())