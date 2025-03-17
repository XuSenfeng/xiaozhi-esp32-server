from config.logger import setup_logging
import os
import re
import time
import random
import asyncio
import difflib
import traceback
from pathlib import Path
from core.utils import p3
from core.handle.sendAudioHandle import send_stt_message
from plugins_func.register import register_function,ToolType, ActionResponse, Action
from core.utils.util import get_string_no_punctuation_or_emoji


TAG = __name__
logger = setup_logging()

NOVEL_CACHE = {}

play_novel_function_desc = {
                "type": "function",
                "function": {
                    "name": "play_novel",
                    "description": "小说、故事、文章方法。比如用户说播放小说，参数为：random，比如用户说播放朝花夕拾，参数为：朝花夕拾",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "song_name": {
                                "type": "string",
                                "description": "文章名称，如果没有指定具体文章则为'random'"
                            }
                        },
                        "required": ["song_name"]
                    }
                }
            }


@register_function('play_novel', play_novel_function_desc, ToolType.SYSTEM_CTL)
def play_novel(conn, song_name: str):
    try:
        novel_intent = f"播放小说 {song_name}" if song_name != "random" else "随机播放小说"

        # 检查事件循环状态
        if not conn.loop.is_running():
            logger.bind(tag=TAG).error("事件循环未运行，无法提交任务")
            return ActionResponse(action=Action.RESPONSE, result="系统繁忙", response="请稍后再试")

        # 提交异步任务
        future = asyncio.run_coroutine_threadsafe(
            handle_novel_command(conn, novel_intent),
            conn.loop
        )

        # 非阻塞回调处理
        def handle_done(f):
            try:
                f.result()  # 可在此处理成功逻辑
                logger.bind(tag=TAG).info("播放完成")
            except Exception as e:
                logger.bind(tag=TAG).error(f"播放失败: {e}")

        future.add_done_callback(handle_done)

        return ActionResponse(action=Action.RESPONSE, result="指令已接收", response="正在为您播放小说")
    except Exception as e:
        logger.bind(tag=TAG).error(f"处理小说意图错误: {e}")
        return ActionResponse(action=Action.RESPONSE, result=str(e), response="播放小说时出错了")


def _extract_song_name(text):
    """从用户输入中提取歌名"""
    for keyword in ["播放小说"]:
        if keyword in text:
            parts = text.split(keyword)
            if len(parts) > 1:
                return parts[1].strip()
    return None


def _find_best_match(potential_song, novel_files):
    """查找最匹配的小说"""
    best_match = None
    highest_ratio = 0

    for novel_file in novel_files:
        song_name = os.path.splitext(novel_file)[0]
        ratio = difflib.SequenceMatcher(None, potential_song, song_name).ratio()
        if ratio > highest_ratio and ratio > 0.4:
            highest_ratio = ratio
            best_match = novel_file
    return best_match


def get_novel_files(novel_dir, novel_ext):
    novel_dir = Path(novel_dir)
    novel_files = []
    novel_file_names = []
    for file in novel_dir.rglob("*"):
        # 判断是否是文件
        if file.is_file():
            # 获取文件扩展名
            ext = file.suffix.lower()
            # 判断扩展名是否在列表中
            if ext in novel_ext:
                # 添加相对路径
                novel_files.append(str(file.relative_to(novel_dir)))
                novel_file_names.append(os.path.splitext(str(file.relative_to(novel_dir)))[0])
    return novel_files, novel_file_names


def initialize_novel_handler(conn):
    global NOVEL_CACHE
    if NOVEL_CACHE == {}:
        logger.bind(tag=TAG).info(f"实例化小说:")
        if "novel" in conn.config:
            NOVEL_CACHE["novel_config"] = conn.config["novel"]
            NOVEL_CACHE["novel_dir"] = os.path.abspath(
                NOVEL_CACHE["novel_config"].get("novel_dir", "./novel")  # 默认路径修改
            )
            NOVEL_CACHE["novel_ext"] = NOVEL_CACHE["novel_config"].get("novel_ext", ("txt"))
            NOVEL_CACHE["refresh_time"] = NOVEL_CACHE["novel_config"].get("refresh_time", 60)
        else:
            NOVEL_CACHE["novel_dir"] = os.path.abspath("./novel")
            NOVEL_CACHE["novel_ext"] = ("txt")
            NOVEL_CACHE["refresh_time"] = 60
        # 获取小说文件列表
        NOVEL_CACHE["novel_files"], NOVEL_CACHE["novel_file_names"] = get_novel_files(NOVEL_CACHE["novel_dir"],
                                                                                      NOVEL_CACHE["novel_ext"])
        NOVEL_CACHE["scan_time"] = time.time()
    return NOVEL_CACHE


async def handle_novel_command(conn, text):
    initialize_novel_handler(conn)
    global NOVEL_CACHE

    """处理小说播放指令"""
    clean_text = re.sub(r'[^\w\s]', '', text).strip()
    logger.bind(tag=TAG).debug(f"检查是否是小说命令: {clean_text}")

    # 尝试匹配具体歌名
    if os.path.exists(NOVEL_CACHE["novel_dir"]):
        if time.time() - NOVEL_CACHE["scan_time"] > NOVEL_CACHE["refresh_time"]:
            # 刷新小说文件列表
            NOVEL_CACHE["novel_files"], NOVEL_CACHE["novel_file_names"] = get_novel_files(NOVEL_CACHE["novel_dir"],
                                                                                          NOVEL_CACHE["novel_ext"])
            NOVEL_CACHE["scan_time"] = time.time()

        potential_song = _extract_song_name(clean_text)
        if potential_song:
            best_match = _find_best_match(potential_song, NOVEL_CACHE["novel_files"])
            if best_match:
                logger.bind(tag=TAG).info(f"找到最匹配的歌曲: {best_match}")
                await play_local_novel(conn, specific_file=best_match)
                return True
    # 检查是否是通用播放小说命令
    await play_local_novel(conn)
    return True


async def play_local_novel(conn, specific_file=None):
    global NOVEL_CACHE
    """播放本地小说文件"""
    try:
        if not os.path.exists(NOVEL_CACHE["novel_dir"]):
            logger.bind(tag=TAG).error(f"小说目录不存在: " + NOVEL_CACHE["novel_dir"])
            return

        # 确保路径正确性
        if specific_file:
            selected_novel = specific_file
            novel_path = os.path.join(NOVEL_CACHE["novel_dir"], specific_file)
        else:
            if not NOVEL_CACHE["novel_files"]:
                logger.bind(tag=TAG).error("未找到TXT小说文件")
                return
            selected_novel = random.choice(NOVEL_CACHE["novel_files"])
            novel_path = os.path.join(NOVEL_CACHE["novel_dir"], selected_novel)

        if not os.path.exists(novel_path):
            logger.bind(tag=TAG).error(f"选定的小说文件不存在: {novel_path}")
            return
        text = f"正在播放{selected_novel}"
        # await send_stt_message(conn, text)
        # conn.tts_first_text_index = 0
        # conn.tts_last_text_index = 0

        # conn.llm_finish_task = True
        # if novel_path.endswith(".p3"):
        #     opus_packets, duration = p3.decode_opus_from_file(novel_path)
        # else:
        #     opus_packets, duration = conn.tts.audio_to_opus_data(novel_path)
        # conn.audio_play_queue.put((opus_packets, selected_novel, 0))

        conn.llm_finish_task = False
        text_index = 0
        processed_chars = 0
        response_message = []

        # 打开文件并逐行读取数据
        with open(novel_path, "r", encoding="utf-8") as file:
            llm_responses = file.readlines()

        # 用于存储分割后的句子  
        split_sentences = []  

        # 定义句子分割的正则表达式  
        # 这里我们使用句号、问号、感叹号和中文标点来分割句子  
        sentence_pattern = r'([。？！]|[\.\?\!])'  

        # 遍历每一行  
        for line in llm_responses:  
            # 按照定义的正则表达式进行分割  
            sentences = re.split(sentence_pattern, line)  
            current_sentence = ''  
            for idx, sentence in enumerate(sentences):  
                # 如果当前句子为空，则将其保存  
                if sentence.strip():  
                    current_sentence += sentence  
                # 如果当前句子不为空，但不是最后一个分割的部分，则添加对应的标点符号，并保存到结果中  
                if sentence.strip() and idx < len(sentences) - 1:  
                    current_sentence += sentences[idx + 1]  
                    split_sentences.append(current_sentence)  
                    current_sentence = '' 

        # print(llm_responses)
        for content in split_sentences:
            await send_stt_message(conn, text)
            response_message.append(content)
            if conn.client_abort:
                break

            # 合并当前全部文本并处理未分割部分
            full_text = "".join(response_message)
            current_text = full_text[processed_chars:]  # 从未处理的位置开始

            # 查找最后一个有效标点
            punctuations = ("。", "？", "！", "；", "：", ",", ".",";", "，", ":")
            last_punct_pos = -1
            for punct in punctuations:
                pos = current_text.rfind(punct)
                if pos > last_punct_pos:
                    last_punct_pos = pos

            # 找到分割点则处理
            if last_punct_pos != -1:
                segment_text_raw = current_text[:last_punct_pos + 1]
                segment_text = get_string_no_punctuation_or_emoji(segment_text_raw)
                if segment_text:
                    # 强制设置空字符，测试TTS出错返回语音的健壮性
                    # if text_index % 2 == 0:
                    #     segment_text = " "
                    text_index += 1
                    conn.recode_first_last_text(segment_text, text_index)
                    future = conn.executor.submit(conn.speak_and_play, segment_text, text_index)
                    conn.tts_queue.put(future)
                    processed_chars += len(segment_text_raw)  # 更新已处理字符位置
                

            # 处理最后剩余的文本
            full_text = "".join(response_message)
            remaining_text = full_text[processed_chars:]
            if remaining_text:
                segment_text = get_string_no_punctuation_or_emoji(remaining_text)
                if segment_text:
                    text_index += 1
                    conn.recode_first_last_text(segment_text, text_index)
                    future = conn.executor.submit(conn.speak_and_play, segment_text, text_index)
                    conn.tts_queue.put(future)


    except Exception as e:
        logger.bind(tag=TAG).error(f"播放小说失败: {str(e)}")
        logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
