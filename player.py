import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')  # 添加这行
from gi.repository import Gst, GLib, GstPbutils  # 添加 GstPbutils
import os
import random
import time
import logging
from pathlib import Path
from typing import List, Optional

class RandomVideoPlayer:
    def __init__(self, directory: str):
        Gst.init(None)
        
        # 创建主要组件
        self.create_pipeline()
        
        # 视频文件管理
        self.directory = Path(directory).resolve()  # 转换为绝对路径
        self.video_files: List[Path] = []
        self.current_video: Optional[Path] = None
        self.next_video: Optional[Path] = None
        self.scan_directory()
        
        # 状态管理
        self.is_paused = False
        self.current_position = 0
        self.duration = 0
        
        # 设置日志和缓存
        self.setup_logging()
        self.video_durations = {}
        
        # 设置bus监听
        self.setup_bus_watch()

        self.timer_id = None
        
    def create_pipeline(self):
        """创建GStreamer管道"""
        self.pipeline = Gst.Pipeline.new()
        
        # 创建元素
        self.playbin = Gst.ElementFactory.make('playbin', 'playbin')
        if not self.playbin:
            raise RuntimeError("无法创建playbin元素")
            
        # 简化管道，直接使用playbin
        self.pipeline.add(self.playbin)
        
        # 配置视频输出
        videosink = Gst.ElementFactory.make('autovideosink', 'videosink')
        if not videosink:
            raise RuntimeError("无法创建videosink元素")
        
        # 设置videosink
        self.playbin.set_property('video-sink', videosink)

    def scan_directory(self):
        """扫描目录下的视频文件"""
        video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
        self.video_files = [
            p for p in self.directory.rglob('*')
            if p.suffix.lower() in video_extensions and p.is_file()
        ]
        if not self.video_files:
            raise RuntimeError(f"在目录 {self.directory} 中未找到视频文件")
            
    def setup_logging(self):
        """配置日志系统"""
        log_file = self.directory / 'video_player.log'
        logging.basicConfig(
            filename=str(log_file),
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger('VideoPlayer')

    def setup_bus_watch(self):
        """设置GStreamer总线监听"""
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self.on_bus_message)

    def on_bus_message(self, bus, message):
        """处理GStreamer总线消息"""
        t = message.type
        if t == Gst.MessageType.EOS:
            self.switch_video()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.logger.error(f"错误: {err}, {debug}")
            self.switch_video()

    def get_video_duration(self, video_path: Path) -> float:
        """获取视频时长（带缓存）"""
        if video_path in self.video_durations:
            return self.video_durations[video_path]
            
        try:
            discoverer = GstPbutils.Discoverer()
            uri = f"file://{video_path.absolute()}"  # 使用绝对路径
            info = discoverer.discover_uri(uri)
            duration = info.get_duration() / Gst.SECOND
            self.video_durations[video_path] = duration
            return duration
        except Exception as e:
            self.logger.error(f"无法获取视频时长 {video_path}: {e}")
            return 0

    def prepare_next_video(self, video_path: Path, position: int):
        """准备下一个视频片段"""
        try:
            uri = f"file://{video_path.absolute()}"  # 使用绝对路径
            self.playbin.set_property('uri', uri)
            
            # 设置播放位置
            self.playbin.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                position * Gst.SECOND * Gst.NSECOND
            )
            
            # 记录当前状态
            self.current_video = video_path
            self.current_position = position
            
            self.logger.info(f"准备播放: {video_path.name} 从 {position}秒开始")
            
        except Exception as e:
            self.logger.error(f"准备视频失败 {video_path}: {e}")
            self.switch_video()

    def switch_video(self) -> bool:
        """切换到新的随机视频片段"""
        if self.is_paused:
            return True
            
        try:
            # 如果存在旧的定时器，移除它
            if self.timer_id is not None:
                GLib.source_remove(self.timer_id)
                self.timer_id = None
            
            # 选择新视频
            new_video = random.choice(self.video_files)
            while new_video == self.current_video and len(self.video_files) > 1:
                new_video = random.choice(self.video_files)
                
            # 获取视频时长并选择随机位置
            duration = self.get_video_duration(new_video)
            if duration <= 15:
                new_position = 0
            else:
                new_position = random.randint(0, int(duration - 15))
                
            # 准备并切换到新视频
            self.prepare_next_video(new_video, new_position)
            
            # 设置新的定时器并保存ID
            self.timer_id = GLib.timeout_add(
                7000,
                self.switch_video
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"切换视频失败: {e}")
            return False

    def cleanup(self):
        """清理资源"""
        if self.timer_id is not None:
            GLib.source_remove(self.timer_id)
            self.timer_id = None
        self.pipeline.set_state(Gst.State.NULL)

    def run(self):
        """启动播放器"""
        try:
            # 开始第一个视频
            self.switch_video()
            self.pipeline.set_state(Gst.State.PLAYING)
            
            # 启动主循环
            loop = GLib.MainLoop()
            loop.run()
            
        except Exception as e:
            self.logger.error(f"播放器运行失败: {e}")
            raise
        finally:
            self.pipeline.set_state(Gst.State.NULL)

    def toggle_pause(self):
        """切换暂停状态"""
        self.is_paused = not self.is_paused
        state = Gst.State.PAUSED if self.is_paused else Gst.State.PLAYING
        self.pipeline.set_state(state)
        self.logger.info(f"播放器状态: {'暂停' if self.is_paused else '播放'}")

if __name__ == '__main__':
    player = None
    try:
        player = RandomVideoPlayer('./videos')
        player.run()
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"发生错误: {e}")
    finally:
        if player:
            player.cleanup()