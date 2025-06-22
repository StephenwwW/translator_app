# -*- coding: utf-8 -*-
"""
多功能雙向翻譯器 (TranslatorApp) - v1.1 (GitHub Release)

這是一個使用 PyQt6 開發的桌面應用程式，整合了多種線上翻譯服務和文字轉語音 (TTS) 引擎。
此版本整合了動態語音選擇功能，並採用了穩定可靠的同步語音播放邏輯，以確保在不同系統環境下的兼容性。

主要功能:
- **多引擎翻譯**: 支援 Groq (Llama3)、Google 免費版、Gemini Pro、DeepL 等。
- **多引擎語音合成**: 整合 Microsoft Edge TTS、Google TTS 和系統原生 TTS (pyttsx3)。
- **動態語音選擇**: 自動載入並提供 Edge TTS 和系統 TTS 的所有可用語音，並以階層式選單呈現。
- **即時雙向翻譯**: 可隨時交換來源與目標語言，並在輸入時自動翻譯。
- **API Key 管理**: 圖形化介面，安全地儲存和管理各項付費服務的 API Key。
- **AI 翻譯優化**: 提供「學習此翻譯」功能，利用範例優化大型語言模型 (LLM) 的翻譯風格。
- **穩健的非同步處理**: 在背景執行緒中載入語音列表，避免應用程式啟動時卡頓。
- **自動化資源管理**: 自動創建和清理臨時生成的語音檔案。

"""
import sys
import random
import os
import json
import asyncio
import threading
import tempfile
import atexit
from collections import defaultdict

# --- PyQt6 核心組件 ---
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                           QHBoxLayout, QTextEdit, QComboBox, QPushButton,
                           QLabel, QGridLayout, QLineEdit, QMessageBox, QFrame)
from PyQt6.QtCore import Qt, QUrl, QSettings, QTimer, QObject, pyqtSignal

# --- 翻譯與 AI 服務 ---
from deep_translator import GoogleTranslator
from groq import Groq
import translators as ts
import deepl
import google.generativeai as genai
import opencc

# --- 語音合成 (TTS) 服務 ---
import edge_tts
import pyttsx3
from gtts import gTTS
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


class VoiceLoader(QObject):
    """
    一個在背景執行緒中工作的 QObject，用於非同步載入線上語音列表，避免阻塞主 UI。
    載入完成後會透過信號通知主執行緒。
    """
    edge_voices_loaded = pyqtSignal(dict)
    pyttsx3_voices_loaded = pyqtSignal(dict)
    error_occurred = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        # 使用 defaultdict 簡化多層級字典的創建
        self.edge_tts_voices = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    async def _fetch_edge_voices_async(self):
        """非同步核心任務：從 edge-tts 服務獲取所有可用語音。"""
        try:
            voices = await edge_tts.VoicesManager.create()
            lang_map = {'zh': '中文', 'en': '英文', 'ja': '日文', 'ko': '韓文', 'fr': '法文', 'de': '德文', 'es': '西班牙文'}
            region_map = {
                'CN': '中國', 'TW': '台灣', 'HK': '香港',
                'US': '美國', 'GB': '英國', 'AU': '澳洲', 'CA': '加拿大', 'IN': '印度'
            }

            for voice in voices.voices:
                locale_parts = voice['Locale'].split('-')
                lang_code, region_code = locale_parts[0], locale_parts[1]

                lang_group = lang_map.get(lang_code, lang_code)
                region_name = region_map.get(region_code, region_code)
                full_region_name = f"{region_name} ({voice['Locale']})"
                gender = voice['Gender']

                self.edge_tts_voices[lang_group][full_region_name][gender].append(voice['ShortName'])

            self.edge_voices_loaded.emit(self.edge_tts_voices)
        except Exception as e:
            self.error_occurred.emit("Edge TTS 載入失敗", f"無法獲取線上語音列表，請檢查網路連線。\n{e}")

    def run_edge_voices_fetch(self):
        """為 _fetch_edge_voices_async 創建並運行一個新的 asyncio 事件循環。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._fetch_edge_voices_async())
        loop.close()

    def load_pyttsx3_voices(self):
        """載入本機 pyttsx3 引擎可用的語音。"""
        try:
            engine = pyttsx3.init()
            voices = engine.getProperty('voices')
            engine.stop()
            pyttsx3_voice_map = {v.name: v.id for v in voices}
            self.pyttsx3_voices_loaded.emit(pyttsx3_voice_map)
        except Exception as e:
            self.error_occurred.emit("pyttsx3 載入失敗", f"無法載入系統內建語音。\n{e}")


class TranslatorApp(QMainWindow):
    """
    主應用程式視窗類別，包含所有 UI 元素和核心功能邏輯。
    """
    def __init__(self):
        """應用程式的建構函式，初始化所有元件。"""
        super().__init__()
        self.setWindowTitle("多功能雙向翻譯器 v1.1")
        self.setGeometry(100, 100, 1200, 800)

        # --- 內部狀態變數 ---
        self.translate_left_to_right = True
        self.last_source_text = ""
        self.temp_files = []
        self.current_playing_file = None
        self.edge_tts_voices = {}
        self.pyttsx3_voices = {}

        # --- 初始化設定、計時器和清理 ---
        self.settings = QSettings('TranslatorApp', 'Settings')
        self.load_settings()

        self.translate_timer = QTimer()
        self.translate_timer.setSingleShot(True)
        self.translate_timer.timeout.connect(self.translate_text)
        atexit.register(self.cleanup_all_temp_files) # 確保程式退出時清理檔案

        # --- 初始化媒體播放器和TTS引擎 ---
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.mediaStatusChanged.connect(self.handle_media_status_changed)
        self.tts_engine = pyttsx3.init()

        # --- 初始化 UI ---
        self.init_ui()

        # --- 在背景非同步載入語音列表 ---
        self.load_voices_in_background()

    def init_ui(self):
        """創建和佈局所有使用者介面 (UI) 元件。"""
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QGridLayout(main_widget)
        
        settings_frame = self._create_settings_frame()
        left_widget = self._create_translation_panel("來源語言:", "輸入文本:")
        self.left_lang_combo = left_widget.findChild(QComboBox)
        self.left_text = left_widget.findChild(QTextEdit)
        self.left_speak_btn = left_widget.findChild(QPushButton)
        
        right_widget = self._create_translation_panel("目標語言:", "翻譯結果:", is_target=True)
        self.right_lang_combo = right_widget.findChild(QComboBox)
        self.right_text = right_widget.findChild(QTextEdit)
        self.right_speak_btn = right_widget.findChild(QPushButton)
        
        correction_widget = self._create_correction_panel()
        right_widget.layout().addWidget(correction_widget)
        
        self.translate_btn = QPushButton("手動翻譯 (Translate)")
        self.swap_btn = QPushButton("交換語言 (Swap) ↔")
        
        layout.addWidget(settings_frame, 0, 0, 1, 2)
        layout.addWidget(left_widget, 1, 0)
        layout.addWidget(right_widget, 1, 1)
        layout.addWidget(self.translate_btn, 2, 0)
        layout.addWidget(self.swap_btn, 2, 1)
        
        self._connect_signals()
        self.update_editor_states()
        self.handle_voice_service_changed()

    def _create_settings_frame(self):
        """創建頂部的設定框架，包含翻譯和語音服務選項。"""
        settings_frame = QFrame()
        settings_frame.setFrameShape(QFrame.Shape.StyledPanel)
        settings_layout = QVBoxLayout(settings_frame)
        
        # 翻譯服務設定
        translate_settings_layout = QHBoxLayout()
        self.translator_combo = QComboBox()
        self.translator_combo.addItems([
            "Groq Llama3 (免費)", "Google翻譯 (免費)", "Translators 聚合翻譯 (免費)",
            "Gemini Pro API (付費)", "DeepL API (付費)"
        ])
        self.api_key_input = QLineEdit(placeholderText="選擇服務後，在此輸入 API Key")
        self.save_api_btn = QPushButton("保存API Key")
        translate_settings_layout.addWidget(QLabel("翻譯服務:"))
        translate_settings_layout.addWidget(self.translator_combo, 1)
        translate_settings_layout.addWidget(QLabel("API Key:"))
        translate_settings_layout.addWidget(self.api_key_input, 2)
        translate_settings_layout.addWidget(self.save_api_btn)
        
        # 語音服務設定
        voice_settings_layout = QHBoxLayout()
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(["Edge TTS", "Google TTS", "System TTS (pyttsx3)"])
        voice_settings_layout.addWidget(QLabel("語音服務:"))
        voice_settings_layout.addWidget(self.voice_combo)
        voice_settings_layout.addStretch(1)
        
        settings_layout.addLayout(translate_settings_layout)
        settings_layout.addLayout(voice_settings_layout)
        
        # 動態語音選項的容器
        self.voice_options_widget = QWidget()
        self.voice_options_layout = QHBoxLayout(self.voice_options_widget)
        self.voice_options_layout.setContentsMargins(0, 5, 0, 0)
        settings_layout.addWidget(self.voice_options_widget)
        
        return settings_frame

    def _create_translation_panel(self, lang_label, text_label, is_target=False):
        """工廠函式，創建一個包含語言選單、文本框和播放按鈕的翻譯面板。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        lang_combo = QComboBox()
        lang_combo.addItems(["中文", "英文", "日文"])
        if is_target:
            lang_combo.setCurrentIndex(1) # 預設目標為英文
        text_edit = QTextEdit()
        speak_btn = QPushButton("▶ 播放語音")
        layout.addWidget(QLabel(lang_label))
        layout.addWidget(lang_combo)
        layout.addWidget(QLabel(text_label))
        layout.addWidget(text_edit, 1)
        layout.addWidget(speak_btn)
        return panel

    def _create_correction_panel(self):
        """創建用於優化 AI 翻譯的「學習」面板。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 10, 0, 0)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)
        layout.addWidget(QLabel("提供更好的翻譯 (可選):"))
        self.correction_input = QLineEdit(placeholderText="若您不滿意，請在此輸入您的版本")
        self.learn_button = QPushButton("✓ 學習此翻譯")
        layout.addWidget(self.correction_input)
        layout.addWidget(self.learn_button)
        return panel

    def _connect_signals(self):
        """集中管理所有 UI 元件的信號和槽的連接。"""
        self.translator_combo.currentTextChanged.connect(self.handle_translator_service_changed)
        self.voice_combo.currentTextChanged.connect(self.handle_voice_service_changed)
        self.save_api_btn.clicked.connect(self.save_api_key)
        self.left_text.textChanged.connect(lambda: self.handle_text_changed(is_left=True))
        self.right_text.textChanged.connect(lambda: self.handle_text_changed(is_left=False))
        self.left_lang_combo.currentTextChanged.connect(self.trigger_translation)
        self.right_lang_combo.currentTextChanged.connect(self.trigger_translation)
        self.translate_btn.clicked.connect(self.handle_translate_button)
        self.swap_btn.clicked.connect(self.handle_swap_button)
        self.left_speak_btn.clicked.connect(lambda: self.handle_speak_button(self.left_text.toPlainText(), self.get_lang_code(self.left_lang_combo.currentText())))
        self.right_speak_btn.clicked.connect(lambda: self.handle_speak_button(self.right_text.toPlainText(), self.get_lang_code(self.right_lang_combo.currentText())))
        self.learn_button.clicked.connect(self.save_correction)

    def load_voices_in_background(self):
        """創建並啟動背景執行緒來載入語音列表。"""
        self.voice_loader = VoiceLoader()
        # Edge TTS 需要網路，放入執行緒中避免阻塞
        threading.Thread(target=self.voice_loader.run_edge_voices_fetch, daemon=True).start()
        # pyttsx3 讀取本機，也放入執行緒以保持一致性
        threading.Thread(target=self.voice_loader.load_pyttsx3_voices, daemon=True).start()
        
        self.voice_loader.edge_voices_loaded.connect(self.on_edge_voices_loaded)
        self.voice_loader.pyttsx3_voices_loaded.connect(self.on_pyttsx3_voices_loaded)
        self.voice_loader.error_occurred.connect(self.on_voice_load_error)

    def handle_voice_service_changed(self):
        """當主語音服務變更時，動態更新下方的詳細選項 UI。"""
        # 清空現有選項
        for i in reversed(range(self.voice_options_layout.count())):
            widget = self.voice_options_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        
        service = self.voice_combo.currentText()
        if service == "Edge TTS":
            self._create_edge_tts_options()
            if self.edge_tts_voices:
                self.update_edge_lang_groups()
        elif service == "System TTS (pyttsx3)":
            self._create_pyttsx3_options()
            if self.pyttsx3_voices:
                self.pyttsx3_voice_combo.addItems(self.pyttsx3_voices.keys())

    def _create_edge_tts_options(self):
        """創建 Edge TTS 的階層式選項 UI。"""
        self.edge_lang_group_combo = QComboBox(self, toolTip="選擇語言大類")
        self.edge_region_combo = QComboBox(self, toolTip="選擇地區或口音")
        self.edge_gender_combo = QComboBox(self, toolTip="選擇性別")
        self.edge_voice_combo = QComboBox(self, toolTip="選擇最終語音")
        # 添加到佈局
        self.voice_options_layout.addWidget(QLabel("語言:"))
        self.voice_options_layout.addWidget(self.edge_lang_group_combo, 1)
        self.voice_options_layout.addWidget(QLabel("地區:"))
        self.voice_options_layout.addWidget(self.edge_region_combo, 1)
        self.voice_options_layout.addWidget(QLabel("性別:"))
        self.voice_options_layout.addWidget(self.edge_gender_combo, 1)
        self.voice_options_layout.addWidget(QLabel("語音:"))
        self.voice_options_layout.addWidget(self.edge_voice_combo, 2)
        # 連接信號
        self.edge_lang_group_combo.currentTextChanged.connect(self.update_edge_regions)
        self.edge_region_combo.currentTextChanged.connect(self.update_edge_genders)
        self.edge_gender_combo.currentTextChanged.connect(self.update_edge_voices)
        self.edge_lang_group_combo.setEnabled(False) # 初始禁用，等待載入

    def _create_pyttsx3_options(self):
        """創建 pyttsx3 的選項 UI。"""
        self.pyttsx3_voice_combo = QComboBox()
        self.voice_options_layout.addWidget(QLabel("已安裝語音:"))
        self.voice_options_layout.addWidget(self.pyttsx3_voice_combo, 1)
        self.pyttsx3_voice_combo.setEnabled(False)

    def on_edge_voices_loaded(self, voices):
        """背景執行緒完成 Edge TTS 語音載入後的回呼。"""
        self.edge_tts_voices = voices
        if self.voice_combo.currentText() == "Edge TTS":
            self.edge_lang_group_combo.setEnabled(True)
            self.update_edge_lang_groups()

    def on_pyttsx3_voices_loaded(self, voices):
        """背景執行緒完成 pyttsx3 語音載入後的回呼。"""
        self.pyttsx3_voices = voices
        if self.voice_combo.currentText() == "System TTS (pyttsx3)":
            self.pyttsx3_voice_combo.addItems(voices.keys())
            self.pyttsx3_voice_combo.setEnabled(True)

    def on_voice_load_error(self, title, message):
        """處理語音載入失敗的錯誤。"""
        QMessageBox.warning(self, title, message)

    def update_edge_lang_groups(self):
        """更新 Edge TTS 語言群組下拉選單。"""
        self.edge_lang_group_combo.clear()
        self.edge_lang_group_combo.addItems(sorted(self.edge_tts_voices.keys()))
        if "中文" in self.edge_tts_voices:
            self.edge_lang_group_combo.setCurrentText("中文")

    def update_edge_regions(self, lang_group):
        """根據選擇的語言群組，更新地區下拉選單。"""
        if not lang_group: return
        self.edge_region_combo.clear()
        regions = sorted(self.edge_tts_voices.get(lang_group, {}).keys())
        self.edge_region_combo.addItems(regions)

    def update_edge_genders(self, region):
        """根據選擇的地區，更新性別下拉選單。"""
        lang_group = self.edge_lang_group_combo.currentText()
        if not lang_group or not region: return
        self.edge_gender_combo.clear()
        genders = sorted(self.edge_tts_voices.get(lang_group, {}).get(region, {}).keys())
        self.edge_gender_combo.addItems(genders)

    def update_edge_voices(self, gender):
        """根據選擇的性別，更新最終語音下拉選單。"""
        lang_group = self.edge_lang_group_combo.currentText()
        region = self.edge_region_combo.currentText()
        if not lang_group or not region or not gender: return
        self.edge_voice_combo.clear()
        voices = sorted(self.edge_tts_voices.get(lang_group, {}).get(region, {}).get(gender, []))
        self.edge_voice_combo.addItems(voices)

    def load_settings(self):
        """從 QSettings 加載應用程式設定。"""
        self.api_keys = json.loads(self.settings.value('api_keys', '{}'))

    def save_api_key(self):
        """將當前輸入的 API Key 保存到 QSettings。"""
        service = self.translator_combo.currentText()
        api_key = self.api_key_input.text().strip()
        self.api_keys[service] = api_key
        self.settings.setValue('api_keys', json.dumps(self.api_keys))
        QMessageBox.information(self, "成功", f"{service} 的 API Key 已保存")

    def save_correction(self):
        """保存使用者提供的更佳翻譯範例，用於優化 AI 翻譯。"""
        source_text = self.last_source_text.strip()
        corrected_translation = self.correction_input.text().strip()
        if not source_text or not corrected_translation:
            QMessageBox.warning(self, "錯誤", "必須有原文和您修正後的譯文才能學習！")
            return
        examples = json.loads(self.settings.value('translation_examples', '[]'))
        new_example = {"source": source_text, "translation": corrected_translation}
        if new_example not in examples:
            examples.append(new_example)
        examples = examples[-10:] # 只保留最新的 10 個範例
        self.settings.setValue('translation_examples', json.dumps(examples, ensure_ascii=False))
        self.correction_input.clear()
        QMessageBox.information(self, "成功", "這個範例將在未來使用 Llama3 翻譯時作為參考。")

    def handle_translator_service_changed(self, service):
        """當翻譯服務變更時，自動更新 API Key 輸入框並觸發翻譯。"""
        self.api_key_input.setText(self.api_keys.get(service, ""))
        self.trigger_translation()

    def handle_text_changed(self, is_left):
        """當任一文本框內容改變時，延遲觸發自動翻譯。"""
        if is_left != self.translate_left_to_right:
            return # 如果變動的不是當前輸入端，則忽略
        source_widget = self.left_text if is_left else self.right_text
        if not source_widget.toPlainText().strip():
            target_widget = self.right_text if is_left else self.left_text
            target_widget.clear()
            return
        self.translate_timer.start(1200) # 延遲 1.2 秒觸發

    def trigger_translation(self):
        """立即觸發翻譯（有 50ms 延遲以合併快速的連續操作）。"""
        self.translate_timer.start(50)

    def translate_text(self):
        """執行實際的翻譯操作。"""
        # 決定翻譯方向和參數
        if self.translate_left_to_right:
            source_text = self.left_text.toPlainText().strip()
            src_lang_name, dest_lang_name = self.left_lang_combo.currentText(), self.right_lang_combo.currentText()
            target_widget = self.right_text
        else:
            source_text = self.right_text.toPlainText().strip()
            src_lang_name, dest_lang_name = self.right_lang_combo.currentText(), self.left_lang_combo.currentText()
            target_widget = self.left_text

        if not source_text:
            target_widget.clear()
            return

        self.last_source_text = source_text
        service = self.translator_combo.currentText()
        translation = ""
        
        try:
            # --- 各翻譯服務的實現 ---
            if service == "Google翻譯 (免費)":
                src_lang = self.get_lang_code(src_lang_name)["google"]
                dest_lang = self.get_lang_code(dest_lang_name)["google"]
                translator = GoogleTranslator(source=src_lang, target=dest_lang)
                translation = translator.translate(text=source_text)

            elif service == "Groq Llama3 (免費)":
                api_key = self.api_keys.get(service)
                if not api_key: raise ValueError("請先在設定中保存 Groq API Key")
                client = Groq(api_key=api_key)
                examples = json.loads(self.settings.value('translation_examples', '[]'))
                examples_str = ""
                if examples:
                    selected_examples = random.sample(examples, min(len(examples), 3))
                    examples_str += "Here are some examples of my preferred final translation style. Use them as a style guide:\n"
                    for ex in selected_examples:
                        examples_str += f"- Source: \"{ex['source']}\"\n  Preferred Translation: \"{ex['translation']}\"\n"
                
                tc_instruction = "Your response MUST BE in Traditional Chinese (繁體中文) for Taiwan." if dest_lang_name == "中文" else ""
                refinement_prompt = (
                    f"You are a world-class localization expert translating from {src_lang_name} to {dest_lang_name}.\n"
                    f"Critically review and provide the best, most natural-sounding translation for the text below. "
                    f"It must be perfectly idiomatic for a native speaker of {dest_lang_name}.\n"
                    f"{tc_instruction}\n\n{examples_str}\n\n"
                    f"--- TASK ---\nTranslate the following text:\n\"{source_text}\"\n\n"
                    f"Provide ONLY the final, improved translation:")
                
                completion = client.chat.completions.create(
                    messages=[{"role": "user", "content": refinement_prompt}],
                    model="llama3-70b-8192", temperature=0.3)
                translation = completion.choices[0].message.content.strip().strip('"')
                
                if dest_lang_name == "中文":
                    cc = opencc.OpenCC('s2twp')
                    translation = cc.convert(translation)

            elif service == "Gemini Pro API (付費)":
                api_key = self.api_keys.get(service)
                if not api_key: raise ValueError("請先保存 Gemini Pro API Key")
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel('gemini-1.5-pro-latest')
                prompt = f"請將以下「{src_lang_name}」文本翻譯成「{dest_lang_name}」，只需要提供翻譯後的文本即可，不要包含任何額外的解釋或標籤：\n\n{source_text}"
                response = model.generate_content(prompt, safety_settings=[
                    {"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]])
                translation = response.text

            elif service == "Translators 聚合翻譯 (免費)":
                lang_map = {"中文": "zh", "英文": "en", "日文": "ja"}
                src_code = lang_map.get(src_lang_name, "auto")
                dest_code = lang_map.get(dest_lang_name, "en")
                translation = ts.translate_text(query_text=source_text, from_language=src_code, to_language=dest_code, translator='bing')
                if dest_code == 'zh':
                    cc = opencc.OpenCC('s2twp')
                    translation = cc.convert(translation)

            elif service == "DeepL API (付費)":
                api_key = self.api_keys.get(service)
                if not api_key: raise ValueError("請先設置 DeepL API Key")
                translator = deepl.Translator(api_key)
                src_lang = self.get_lang_code(src_lang_name)["deepl"]
                dest_lang = self.get_lang_code(dest_lang_name)["deepl"]
                result = translator.translate_text(source_text, source_lang=src_lang, target_lang=dest_lang)
                translation = result.text
                
            target_widget.setText(translation)
        except Exception as e:
            target_widget.setText(f"翻譯錯誤: {str(e)}")

    def swap_languages(self):
        """交換來源和目標語言，以及對應文本框的內容和狀態。"""
        self.stop_audio()
        left_idx, right_idx = self.left_lang_combo.currentIndex(), self.right_lang_combo.currentIndex()
        self.left_lang_combo.setCurrentIndex(right_idx)
        self.right_lang_combo.setCurrentIndex(left_idx)
        self.left_text.textChanged.disconnect()
        self.right_text.textChanged.disconnect()
        left_content, right_content = self.left_text.toPlainText(), self.right_text.toPlainText()
        self.left_text.setPlainText(right_content)
        self.right_text.setPlainText(left_content)
        self.translate_left_to_right = not self.translate_left_to_right
        self.update_editor_states()
        self.left_text.textChanged.connect(lambda: self.handle_text_changed(True))
        self.right_text.textChanged.connect(lambda: self.handle_text_changed(False))
    
    def update_editor_states(self):
        """根據當前翻譯方向，更新文本框的唯讀狀態和背景色。"""
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
        """當媒體播放結束時，自動清理對應的臨時文件。"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.current_playing_file:
            self.cleanup_single_file(self.current_playing_file)
            self.current_playing_file = None

    def cleanup_single_file(self, file_path):
        """安全地刪除一個臨時文件並從追蹤列表中移除。"""
        if file_path in self.temp_files:
            try:
                os.unlink(file_path)
                self.temp_files.remove(file_path)
            except (PermissionError, OSError) as e:
                print(f"清理文件 {file_path} 時出錯: {e}")

    def cleanup_all_temp_files(self):
        """在程式退出時，清理所有剩餘的臨時文件。"""
        self.stop_audio()
        for file_path in self.temp_files[:]:
            self.cleanup_single_file(file_path)
        print("所有臨時文件已清理。")

    async def text_to_speech(self, text, language_codes):
        """
        異步函式，將文本轉換為語音並保存為臨時文件。
        :param text: 要轉換的文本。
        :param language_codes: 包含各服務所需語言代碼的字典。
        :return: 臨時音訊檔案的路徑，或在失敗時返回 None。
        """
        if not text: return None
        service = self.voice_combo.currentText()
        temp_file_path = None
        try:
            # 創建一個具名的臨時文件，確保它不會被自動刪除
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tf:
                temp_file_path = tf.name

            if service == "Edge TTS":
                voice = self.edge_voice_combo.currentText()
                if not voice: raise ValueError("請先在 Edge TTS 選項中選擇一個語音")
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(temp_file_path)

            elif service == "Google TTS":
                tts = gTTS(text=text, lang=language_codes["google"])
                tts.save(temp_file_path)

            elif service == "System TTS (pyttsx3)":
                voice_name = self.pyttsx3_voice_combo.currentText()
                if voice_name and voice_name in self.pyttsx3_voices:
                    self.tts_engine.setProperty('voice', self.pyttsx3_voices[voice_name])
                self.tts_engine.save_to_file(text, temp_file_path)
                self.tts_engine.runAndWait() # 阻塞直到檔案儲存完畢

            # 確認檔案已成功生成且非空
            if temp_file_path and os.path.getsize(temp_file_path) > 0:
                self.temp_files.append(temp_file_path)
                return temp_file_path
            else: # 如果檔案為空或未創建，則清理並返回 None
                if temp_file_path: self.cleanup_single_file(temp_file_path)
                return None
                
        except Exception as e:
            QMessageBox.critical(self, "TTS Error", f"生成語音時發生錯誤: {e}")
            if temp_file_path: self.cleanup_single_file(temp_file_path)
            return None

    def speak_text(self, text, language_codes):
        """
        將文本轉換為語音並播放。採用同步阻塞方式確保穩定性。
        """
        if not text: return
        self.stop_audio()
        temp_file = None
        
        try:
            # 獲取或創建 asyncio 事件循環
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # 同步等待異步的 TTS 任務完成
            temp_file = loop.run_until_complete(self.text_to_speech(text, language_codes))
            
            if temp_file:
                self.current_playing_file = temp_file
                self.player.setSource(QUrl.fromLocalFile(temp_file))
                self.audio_output.setVolume(0.8) # 0.0 至 1.0
                self.player.play()
        except Exception as e:
            QMessageBox.critical(self, "Playback Error", f"播放音訊時發生錯誤: {e}")
            if temp_file: self.cleanup_single_file(temp_file)

    def stop_audio(self):
        """停止當前播放的音訊。"""
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.stop()

    def handle_speak_button(self, text, language):
        """處理「播放語音」按鈕的點擊事件。"""
        self.speak_text(text, language)

    def handle_translate_button(self):
        """處理「手動翻譯」按鈕的點擊事件。"""
        self.translate_text()

    def handle_swap_button(self):
        """處理「交換語言」按鈕的點擊事件。"""
        self.swap_languages()

    def closeEvent(self, event):
        """處理視窗關閉事件，確保設定被保存、檔案被清理。"""
        self.cleanup_all_temp_files()
        self.settings.sync()
        super().closeEvent(event)

    def get_lang_code(self, language):
        """根據顯示的語言名稱，返回一個包含各服務所需代碼的字典。"""
        lang_map = {"中文": {"google": "zh-TW", "deepl": "ZH"},
                    "英文": {"google": "en", "deepl": "EN-US"},
                    "日文": {"google": "ja", "deepl": "JA"}}
        return lang_map.get(language, {"google": "en", "deepl": "EN"})

def main():
    """
    應用程式主入口點。
    """
    # 解決在 Windows 上 Qt 與 asyncio 的潛在衝突
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    app = QApplication(sys.argv)
    window = TranslatorApp()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()