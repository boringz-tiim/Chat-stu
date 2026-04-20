import sys
import os
import json
import socket
import threading
import time
import base64
import ipaddress
import re
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QStackedWidget, QPushButton, QLineEdit, QMessageBox,
    QListWidget, QListWidgetItem, QTextEdit, QLabel, QWidget, QHBoxLayout, QToolButton,
    QFileDialog, QSizePolicy, QAbstractItemView
)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QIODevice, QObject, Signal, Qt, QUrl
from PySide6.QtGui import QIcon, QDesktopServices

ui_path = r"D:\python-work\Chat_stu\Client_stu\client.ui"
icon_path = r"D:\python-work\Chat_stu\Client_stu\icon.ico"
eye_open = r"D:\python-work\Chat_stu\Client_stu\eye_open.ico"
eye_close = r"D:\python-work\Chat_stu\Client_stu\eye_close.ico"


def send_request(server_ip, server_port, req):
    """发送短连接请求，只在登录注册时使用"""
    with socket.create_connection((server_ip, server_port), timeout=5) as sock:
        sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("服务端断开连接")
            buf.extend(chunk)
            i = buf.find(b"\n")
            if i != -1:
                line = bytes(buf[:i]).decode("utf-8", errors="replace")
                return json.loads(line)


def getLocalIp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except (OSError, socket.error):
        return "127.0.0.1"
    finally:
        s.close()


def recv_line(conn: socket.socket, buf: bytearray):
    """从socket接收一行数据，无限等待（聊天应用不需要超时）"""
    while True:
        i = buf.find(b"\n")
        if i != -1:
            line = bytes(buf[:i])
            del buf[:i + 1]
            return line.decode("utf-8", errors="replace")
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf.extend(chunk)


def recv_exact(conn: socket.socket, size: int, timeout=60):
    """接收固定大小的数据，文件传输时使用超时"""
    conn.settimeout(timeout)
    data = b""
    try:
        while len(data) < size:
            chunk = conn.recv(min(4096, size - len(data)))
            if not chunk:
                raise ConnectionError("文件接收中断")
            data += chunk
        return data
    except socket.timeout:
        raise TimeoutError(f"文件接收超时（{timeout}秒）")
    finally:
        conn.settimeout(None)


def send_json(conn: socket.socket, obj: dict):
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

class FileLabel(QLabel):
    def __init__(self, file_path="", parent=None):
        super().__init__(parent)
        self.file_path = file_path

    def mouseDoubleClickEvent(self, event):
        if self.file_path and os.path.exists(self.file_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.file_path))
        else:
            top = self.window()
            QMessageBox.warning(top, "无法打开", "文件不存在或已被删除")
        super().mouseDoubleClickEvent(event)
class UiEmitter(QObject):
    users_update = Signal(list)
    msg_in = Signal(str, str, object)
    info = Signal(str)


class MainWindow:
    def __init__(self):
        self.sock = None
        self.recv_thread = None
        self.stop_recv = threading.Event()
        self.sock_lock = threading.Lock()
        self.sessions_lock = threading.RLock()

        self.username = ""
        self.local_ip = getLocalIp()

        self.sessions = {}
        self.current_peer = None

        self.server_closed = False
        self.emitter = UiEmitter()

        # UI控件
        self.window = None
        self.interfaceWidget = None
        self.loginButton = None
        self.enrollButton = None
        self.userNameEdit = None
        self.userPwdEdit = None
        self.enrollUserNameEdit = None
        self.enrollUserPwdEdit = None
        self.enrollSummitButton = None
        self.enrollServerIPEdit = None
        self.enrollServerPortEdit = None
        self.serverIPEdit = None
        self.serverPortEdit = None
        self.loginEyeButton = None
        self.chatListWidget = None
        self.messageListWidget = None
        self.inputTextEdit = None
        self.sendMsgButton = None
        self.sendFileButton = None

        self.load_ui()
        self.bind_signals()

        self.emitter.users_update.connect(self.on_users_update)
        self.emitter.msg_in.connect(self.on_message_in)
        self.emitter.info.connect(lambda s: self.popUp("提示", s, ok=True))

        self.last_msg_time = None

        with self.sessions_lock:
            self.sessions["__ALL__"] = {
                "messages": [],
                "unread": 0,
                "online": True,
                "ip": ""
            }

    def load_ui(self):
        loader = QUiLoader()
        file = QFile(ui_path)
        file.open(QIODevice.OpenModeFlag.ReadOnly)
        self.window = loader.load(file)
        file.close()

        self.window.setWindowTitle("客户端")
        self.window.setWindowIcon(QIcon(icon_path))

        self.interfaceWidget = self.window.findChild(QStackedWidget, "interfaceWidget")
        self.loginButton = self.window.findChild(QPushButton, "loginButton")
        self.enrollButton = self.window.findChild(QPushButton, "enrollButton")

        self.userNameEdit = self.window.findChild(QLineEdit, "userNameEdit")
        self.userPwdEdit = self.window.findChild(QLineEdit, "userPwdEdit")

        self.enrollUserNameEdit = self.window.findChild(QLineEdit, "enrollUserNameEdit")
        self.enrollUserPwdEdit = self.window.findChild(QLineEdit, "enrollUserPwdEdit")
        self.enrollSummitButton = self.window.findChild(QPushButton, "enrollSummitButton")
        self.enrollServerIPEdit = self.window.findChild(QLineEdit, "enrollServerIPEdit")
        self.enrollServerPortEdit = self.window.findChild(QLineEdit, "enrollServerPortEdit")

        self.serverIPEdit = self.window.findChild(QLineEdit, "serverIPEdit")
        self.serverPortEdit = self.window.findChild(QLineEdit, "serverPortEdit")

        self.loginEyeButton = self.window.findChild(QToolButton, "loginEyeButton")
        if self.userPwdEdit:
            self.userPwdEdit.setEchoMode(QLineEdit.EchoMode.Password)
        if self.loginEyeButton:
            self.loginEyeButton.setCheckable(True)
            self.loginEyeButton.setIcon(QIcon(eye_close))

        self.chatListWidget = self.window.findChild(QListWidget, "chatListWidget")
        self.messageListWidget = self.window.findChild(QListWidget, "messageListWidget")
        self.inputTextEdit = self.window.findChild(QTextEdit, "inputTextEdit")
        self.sendMsgButton = self.window.findChild(QPushButton, "sendMsgButton")
        self.sendFileButton = self.window.findChild(QPushButton, "sendFileButton")

        self.messageListWidget.setSpacing(3)

        if self.chatListWidget:
            self.chatListWidget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.chatListWidget.setStyleSheet("""
                QListWidget {
                    background: #FFFFFF;
                    border: 1px solid #E5E5E5;
                    outline: 0;
                }
                QListWidget::item {
                    padding: 10px 8px;
                    margin: 2px 6px;
                    border-radius: 8px;
                }
                QListWidget::item:hover {
                    background: #F2F6FF;
                }
                QListWidget::item:selected {
                    background: #DCEBFF;
                    color: #003A8C;
                    font-weight: 600;
                }
            """)

        # 连接双击打开文件信号
        self.messageListWidget.itemDoubleClicked.connect(self.open_file)
    def bind_signals(self):
        self.loginButton.clicked.connect(self.do_login)
        self.enrollButton.clicked.connect(lambda: self.interfaceWidget.setCurrentIndex(1))
        self.enrollSummitButton.clicked.connect(self.do_register)

        if self.sendMsgButton:
            self.sendMsgButton.clicked.connect(self.send_chat_message)
        if self.chatListWidget:
            self.chatListWidget.itemClicked.connect(self.on_peer_clicked)
        if self.sendFileButton:
            self.sendFileButton.clicked.connect(self.send_file)

        if self.loginEyeButton and self.userPwdEdit:
            self.loginEyeButton.toggled.connect(
                lambda checked: self.toggle_password(self.userPwdEdit, self.loginEyeButton, checked))

    def popUp(self, title, msg, ok=True):
        if ok:
            QMessageBox.information(self.window, title, msg)
        else:
            QMessageBox.warning(self.window, title, msg)

    @staticmethod
    def isValidIp(ip):
        if not ip or not isinstance(ip, str):
            return False
        try:
            ipaddress.ip_address(ip.strip())
            return True
        except ValueError:
            return False

    @staticmethod
    def is_valid_username(username: str):
        username = (username or "").strip()
        if not username:
            return False, "用户名不能为空"
        if username[0].isdigit():
            return False, "用户名不能以数字开头"
        return True, ""

    @staticmethod
    def is_strong_password(password: str):
        if len(password) <= 6:
            return False, "密码长度必须大于6位"
        if not re.search(r'[a-zA-Z]', password):
            return False, "密码必须包含字母"
        if not re.search(r'\d', password):
            return False, "密码必须包含数字"
        if not re.search(r'[^a-zA-Z0-9]', password):
            return False, "密码必须包含特殊字符"
        return True, ""

    def do_register(self):
        userName = (self.enrollUserNameEdit.text() if self.enrollUserNameEdit else "")
        password = (self.enrollUserPwdEdit.text() if self.enrollUserPwdEdit else "")
        serverIP = (self.enrollServerIPEdit.text() if self.enrollServerIPEdit else "")
        serverPort = int((self.enrollServerPortEdit.text() if self.enrollServerPortEdit else "0") or "0")

        if not self.isValidIp(serverIP):
            self.popUp("注册失败", "服务器IP格式不合法", ok=False)
            return

        ok_u, msg_u = self.is_valid_username(userName)
        if not ok_u:
            self.popUp("注册失败", msg_u, ok=False)
            return

        ok_p, msg_p = self.is_strong_password(password)
        if not ok_p:
            self.popUp("注册失败", msg_p, ok=False)
            return

        try:
            resp = send_request(serverIP, serverPort, {
                "action": "register",
                "username": userName,
                "password": password
            })
            self.popUp("注册结果", resp.get("msg", ""), ok=resp.get("ok", False))
            if resp.get("ok"):
                self.interfaceWidget.setCurrentIndex(0)
        except Exception as e:
            self.popUp("网络错误", str(e), ok=False)

    def do_login(self):
        username = (self.userNameEdit.text() if self.userNameEdit else "").strip()
        password = (self.userPwdEdit.text() if self.userPwdEdit else "")
        serverIP = (self.serverIPEdit.text() if self.serverIPEdit else "").strip()
        serverPort = int((self.serverPortEdit.text() if self.serverPortEdit else "0") or "0")

        if not self.isValidIp(serverIP):
            self.popUp("登录失败", "服务器IP格式不合法", ok=False)
            return

        if not username or not password:
            self.popUp("登录失败", "用户名/密码不能为空", ok=False)
            return

        try:
            resp = send_request(serverIP, serverPort, {
                "action": "login",
                "username": username,
                "password": password,
                "client_ip": self.local_ip
            })
            self.popUp("登录结果", resp.get("msg", ""), ok=resp.get("ok", False))

            if resp.get("ok"):
                self.username = username
                self.interfaceWidget.setCurrentIndex(2)
                self.start_chat_connection(serverIP, serverPort)

        except Exception as e:
            self.popUp("网络错误", str(e), ok=False)

    @staticmethod
    def toggle_password(edit: QLineEdit, btn: QToolButton, checked: bool):
        if checked:
            edit.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setIcon(QIcon(eye_open))
        else:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setIcon(QIcon(eye_close))

    def start_chat_connection(self, server_ip, server_port):
        self.close_chat_connection()
        try:
            # 连接时设置5秒超时
            self.sock = socket.create_connection((server_ip, server_port), timeout=5)
            # 连接成功后设置为阻塞模式（无超时）
            self.sock.settimeout(None)
            self.stop_recv.clear()

            send_json(self.sock, {
                "action": "online",
                "username": self.username,
                "client_ip": self.local_ip
            })

            self.recv_thread = threading.Thread(target=self.recv_loop, daemon=True)
            self.recv_thread.start()

            self.emitter.info.emit(f"已连接 {server_ip}:{server_port}  本机IP={self.local_ip}")
        except Exception as e:
            self.popUp("连接失败", str(e), ok=False)

    def close_chat_connection(self):
        if self.sock is None:
            return
        try:
            self.stop_recv.set()
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except (OSError, ConnectionError):
                    pass
            self.sock.close()
        finally:
            self.sock = None

    def recv_loop(self):
        buf = bytearray()
        try:
            while not self.stop_recv.is_set():
                line = recv_line(self.sock, buf)
                if line is None:
                    break

                try:
                    msg = json.loads(line)
                except:
                    continue

                action = msg.get("action")

                # ❗只做转发，不做逻辑处理
                self.emitter.msg_in.emit(
                    msg.get("from", ""),
                    action,
                    msg
                )

        except Exception as e:
            if not self.server_closed:
                self.emitter.info.emit(f"连接异常: {e}")
        finally:
            self.close_chat_connection()
    def receive_file_in_background(self, from_user, filename, file_size):
        """在后台线程中接收文件"""
        try:
            server_ip = self.serverIPEdit.text().strip()
            server_port = int(self.serverPortEdit.text().strip())

            with socket.create_connection((server_ip, server_port), timeout=10) as file_sock:
                # 发送文件接收确认
                send_json(file_sock, {"ok": True, "msg": "ready to receive file"})

                # 等待文件传输开始的通知（无限等待）
                buf = bytearray()
                response = recv_line(file_sock, buf)
                if not response:
                    raise ConnectionError("服务器无响应")

                resp = json.loads(response)
                if not resp.get("ok"):
                    raise Exception(resp.get("msg", "接收文件失败"))

                # 接收文件数据（文件传输使用60秒超时）
                save_dir = os.path.join(os.getcwd(), "received_files")
                os.makedirs(save_dir, exist_ok=True)

                base_name = os.path.basename(filename)
                name, ext = os.path.splitext(base_name)
                save_path = os.path.join(save_dir, f"{from_user}_{name}_{int(datetime.now().timestamp())}{ext}")

                with open(save_path, "wb") as f:
                    received = 0
                    while received < file_size:
                        chunk_size = min(4096, file_size - received)
                        chunk = recv_exact(file_sock, chunk_size, timeout=60)
                        f.write(chunk)
                        received += len(chunk)

                # 构造文件消息
                file_msg = {
                    "type": "file",
                    "filename": filename,
                    "path": save_path,
                    "is_me": False,
                    "time": datetime.now(),
                    "from": from_user,
                    "public": True
                }

                with self.sessions_lock:
                    if from_user not in self.sessions:
                        self.sessions[from_user] = {
                            "messages": [],
                            "unread": 0,
                            "online": True,
                            "ip": ""
                        }

                    self.sessions[from_user]["messages"].append(file_msg)

                    if self.current_peer != from_user:
                        self.sessions[from_user]["unread"] += 1
                    else:
                        self.refresh_message_view(from_user)

                self.emitter.info.emit(f"收到文件: {filename}\n保存路径:{save_path}")

        except Exception as e:
            self.emitter.info.emit(f"接收文件失败: {e}")

    def on_users_update(self, users: list):
        """更新用户列表"""
        print(f"[DEBUG] on_users_update 收到用户列表: {users}")

        with self.sessions_lock:
            if users and isinstance(users, list) and len(users) > 0:
                online_users_dict = {}
                for u in users:
                    username = u.get("username")
                    if username:
                        online_users_dict[username] = {
                            "online": u.get("online", False),
                            "ip": u.get("ip", "")
                        }

                for username in list(self.sessions.keys()):
                    if username != "__ALL__" and username != self.username:
                        if username in online_users_dict:
                            self.sessions[username]["online"] = online_users_dict[username]["online"]
                            self.sessions[username]["ip"] = online_users_dict[username]["ip"]
                            print(
                                f"[DEBUG] 更新用户 {username} 状态为: {'在线' if self.sessions[username]['online'] else '离线'}")
                        else:
                            self.sessions[username]["online"] = False

                for username, info in online_users_dict.items():
                    if username != self.username and username not in self.sessions:
                        self.sessions[username] = {
                            "messages": [],
                            "unread": 0,
                            "online": info["online"],
                            "ip": info["ip"]
                        }
                        print(f"[DEBUG] 添加新用户: {username}")
            else:
                print(f"[DEBUG] 收到空用户列表，只刷新显示")

            if not self.chatListWidget:
                return

            # 保存当前选中的用户
            current_selected = None
            if self.chatListWidget.currentItem():
                display_text = self.chatListWidget.currentItem().text()
                if "公聊大厅" in display_text:
                    current_selected = "__ALL__"
                else:
                    username = display_text
                    if username.startswith("[在线]"):
                        username = username[4:]  # 修复：4个字符
                    elif username.startswith("[离线]"):
                        username = username[4:]  # 修复：4个字符
                    if "(" in username:
                        username = username.split("(")[0]
                    current_selected = username
                    print(f"[DEBUG] 当前选中用户: '{current_selected}'")

            self.chatListWidget.clear()

            # 公聊会话
            unread_all = self.sessions.get("__ALL__", {}).get("unread", 0)
            all_text = f"公聊大厅({unread_all})" if unread_all > 0 else "公聊大厅"
            all_item = QListWidgetItem(all_text)
            self.chatListWidget.addItem(all_item)

            # 分类在线和离线用户
            online_users = []
            offline_users = []

            for username, session in self.sessions.items():
                if username == "__ALL__" or username == self.username:
                    continue
                if session.get("online", False):
                    online_users.append(username)
                else:
                    offline_users.append(username)

            print(f"[DEBUG] 在线用户: {online_users}")
            print(f"[DEBUG] 离线用户: {offline_users}")

            # 添加在线用户
            for username in sorted(online_users):
                session = self.sessions[username]
                unread = session.get("unread", 0)
                display_text = f"[在线]{username}({unread})" if unread > 0 else f"[在线]{username}"
                item = QListWidgetItem(display_text)
                self.chatListWidget.addItem(item)
                if current_selected == username:
                    item.setSelected(True)

            # 添加离线用户
            for username in sorted(offline_users):
                display_text = f"[离线]{username}"
                item = QListWidgetItem(display_text)
                self.chatListWidget.addItem(item)
                if current_selected == username:
                    item.setSelected(True)

            if current_selected == "__ALL__":
                all_item.setSelected(True)

    def on_peer_clicked(self, item):
        if item is None:
            return

        display_text = item.text()
        print(f"[DEBUG] on_peer_clicked 点击文本: '{display_text}'")  # 调试

        if "公聊大厅" in display_text:
            self.current_peer = "__ALL__"
        else:
            # 解析用户名
            username = display_text

            # 去掉前面的状态标记（[在线] 或 [离线]）
            if username.startswith("[在线]"):
                username = username[4:]  # [在线] 是4个字符，不是5个！
            elif username.startswith("[离线]"):
                username = username[4:]  # [离线] 也是4个字符

            # 去掉未读数字部分 (xxx)
            if "(" in username:
                username = username.split("(")[0]

            self.current_peer = username.strip()
            print(f"[DEBUG] 解析后的用户名: '{self.current_peer}'")  # 调试

            with self.sessions_lock:
                if self.current_peer in self.sessions:
                    self.sessions[self.current_peer]["unread"] = 0

        self.refresh_message_view(self.current_peer)
        # 刷新用户列表显示（保持现有用户状态）
        users_list = self.build_users_list()
        self.on_users_update(users_list)
    def refresh_message_view(self, peer: str):
        if self.messageListWidget is None:
            return

        self.messageListWidget.clear()
        self.last_msg_time = None

        with self.sessions_lock:
            msgs = self.sessions.get(peer, {}).get("messages", [])

        for m in msgs:
            self.add_message_bubble(m)

    def add_message_bubble(self, msg):
        item = QListWidgetItem()
        item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsSelectable)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 2, 10, 2)

        # 文件消息
        if isinstance(msg, dict) and msg.get("type") == "file":
            filename = msg["filename"]
            path = msg["path"]
            is_me = msg["is_me"]
            msg_time = msg["time"]
            from_ = msg["from"]

            bubble = FileLabel(
                path
            )
            bubble.setText(
                f"{'我' if is_me else from_}:\n"
                f"📄 {filename}\n"
                f"📁 {path}\n"
                f"双击打开"
            )
            bubble.setWordWrap(True)
            bubble.setProperty("file_path", path)

            bubble.setCursor(Qt.CursorShape.PointingHandCursor)

            bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            bubble.setMaximumWidth(400)
            bubble.setStyleSheet("""
                QLabel{
                    padding:10px;
                    border-radius:10px;
                    background:#E8F0FE;
                    border:1px solid #C3D3F5;
                }
            """)

            item.setData(Qt.ItemDataRole.UserRole, path)
            if msg.get("public"):
                bubble.setText(bubble.text() + "\n🌐 公聊文件")

            if self.last_msg_time is None or (msg_time - self.last_msg_time).total_seconds() >= 120:
                self.add_time_item(msg_time.strftime("%H:%M"))
                self.last_msg_time = msg_time

            if is_me:
                layout.addStretch()
                layout.addWidget(bubble)
            else:
                layout.addWidget(bubble)
                layout.addStretch()

        # 文本消息
        elif isinstance(msg, tuple) and len(msg) >= 2:
            is_me, text = msg[0], msg[1]
            msg_time = msg[2] if len(msg) > 2 else datetime.now()

            bubble = QLabel(text)
            bubble.setWordWrap(True)
            bubble.setMaximumWidth(360)
            bubble.setStyleSheet("""
                QLabel{
                    padding:8px 10px;
                    border-radius:10px;
                    background:#DCF8C6;
                }
            """ if is_me else """
                QLabel{
                    padding:8px 10px;
                    border-radius:10px;
                    background:#FFFFFF;
                    border:1px solid #E5E5E5;
                }
            """)

            if self.last_msg_time is None or (msg_time - self.last_msg_time).total_seconds() >= 120:
                self.add_time_item(msg_time.strftime("%H:%M"))
                self.last_msg_time = msg_time

            if is_me:
                layout.addStretch()
                layout.addWidget(bubble)
            else:
                layout.addWidget(bubble)
                layout.addStretch()
        else:
            return

        self.messageListWidget.addItem(item)
        self.messageListWidget.setItemWidget(item, container)
        container.adjustSize()
        item.setSizeHint(container.sizeHint())
        self.messageListWidget.scrollToBottom()

    def build_users_list(self):
        """构建用户列表（用于发送给UI）"""
        with self.sessions_lock:
            users = []
            for username, session in self.sessions.items():
                if username != "__ALL__" and username != self.username:
                    users.append({
                        "username": username,
                        "online": session.get("online", False),
                        "ip": session.get("ip", "")
                    })
            print(f"[DEBUG] build_users_list 返回: {users}")  # 调试
            return users

    def on_message_in(self, _from, action, msg):
        if action == "private_msg":
            peer = _from
            # ✅ 判断是不是文件
            if msg.get("type") == "file":
                content = msg
            else:
                content = msg.get("content")

            # ✅ 先确保会话存在
            with self.sessions_lock:
                if peer not in self.sessions:
                    self.sessions[peer] = {
                        "messages": [],
                        "unread": 0,
                        "online": True,
                        "ip": ""
                    }

            # =====================
            # ✅ 文件消息处理
            # =====================
            if msg.get("type") == "file":

                filename = msg.get("filename")
                data = msg.get("data")

                save_dir = os.path.join(os.getcwd(), "received_files")
                os.makedirs(save_dir, exist_ok=True)

                # 防止重名覆盖
                save_path = os.path.join(
                    save_dir,
                    f"{int(time.time())}_{filename}"
                )

                try:
                    with open(save_path, "wb") as f:
                        f.write(base64.b64decode(data))
                except Exception as e:
                    self.emitter.info.emit(f"文件保存失败: {e}")
                    return

                file_msg = {
                    "type": "file",
                    "filename": filename,
                    "path": save_path,
                    "is_me": False,
                    "time": datetime.now(),
                    "from": _from
                }

                with self.sessions_lock:
                    self.sessions[peer]["messages"].append(file_msg)

                    if self.current_peer != peer:
                        self.sessions[peer]["unread"] += 1
                    else:
                        self.refresh_message_view(peer)

            # =====================
            # ✅ 普通文本消息
            # =====================
            else:
                content = msg.get("content", "")

                with self.sessions_lock:
                    if isinstance(content, dict):
                        # 文件
                        self.sessions[peer]["messages"].append(content)
                    else:
                        # 文本
                        self.sessions[peer]["messages"].append(
                            (False, content, datetime.now())
                        )

                    if self.current_peer != peer:
                        self.sessions[peer]["unread"] += 1
                    else:
                        self.refresh_message_view(peer)
        elif action == "public_msg":

            # ======================
            # 📄 文件消息
            # ======================
            if msg.get("type") == "file":
                from_user = msg.get("from")
                filename = msg.get("filename")
                data = msg.get("data")

                save_dir = os.path.join(os.getcwd(), "received_files")
                os.makedirs(save_dir, exist_ok=True)

                save_path = os.path.join(
                    save_dir,
                    f"{from_user}_{int(datetime.now().timestamp())}_{filename}"
                )

                try:
                    with open(save_path, "wb") as f:
                        f.write(base64.b64decode(data))
                except Exception as e:
                    self.emitter.info.emit(f"群文件保存失败: {e}")
                    return

                file_msg = {
                    "type": "file",
                    "filename": filename,
                    "path": save_path,
                    "is_me": False,
                    "time": datetime.now(),
                    "from": from_user,
                    "public": True
                }

                with self.sessions_lock:
                    self.sessions["__ALL__"]["messages"].append(file_msg)

                    if self.current_peer != "__ALL__":
                        self.sessions["__ALL__"]["unread"] += 1
                    else:
                        self.refresh_message_view("__ALL__")

                self.emitter.info.emit(f"收到群文件: {filename}")

            # ======================
            # 💬 文本消息
            # ======================
            else:
                content = msg.get("content", "")
                from_user = msg.get("from")

                with self.sessions_lock:
                    self.sessions["__ALL__"]["messages"].append(
                        (False, f"{from_user}: {content}", datetime.now())
                    )

                    if self.current_peer != "__ALL__":
                        self.sessions["__ALL__"]["unread"] += 1
                    else:
                        self.refresh_message_view("__ALL__")

        # =====================
        # 群聊（暂不处理文件）
        # =====================
        # elif action == "public_file":
        #     from_user = msg.get("from")
        #     filename = msg.get("filename")
        #     data = msg.get("data")
        #
        #     if not data:
        #         return
        #
        #     import base64
        #     import os
        #     from datetime import datetime
        #
        #     save_dir = os.path.join(os.getcwd(), "received_files")
        #     os.makedirs(save_dir, exist_ok=True)
        #
        #     save_path = os.path.join(
        #         save_dir,
        #         f"{from_user}_{int(datetime.now().timestamp())}_{filename}"
        #     )
        #
        #     try:
        #         with open(save_path, "wb") as f:
        #             f.write(base64.b64decode(data))
        #     except Exception as e:
        #         self.emitter.info.emit(f"群文件保存失败: {e}")
        #         return
        #
        #     file_msg = {
        #         "type": "file",
        #         "filename": filename,
        #         "path": save_path,
        #         "is_me": False,
        #         "time": datetime.now(),
        #         "from": from_user,
        #         "public": True
        #     }
        #
        #     with self.sessions_lock:
        #         if "__ALL__" not in self.sessions:
        #             self.sessions["__ALL__"] = {
        #                 "messages": [],
        #                 "unread": 0,
        #                 "online": True,
        #                 "ip": ""
        #             }
        #
        #         self.sessions["__ALL__"]["messages"].append(file_msg)
        #
        #         if self.current_peer != "__ALL__":
        #             self.sessions["__ALL__"]["unread"] += 1
        #         else:
        #             self.refresh_message_view("__ALL__")
        #
        #     self.emitter.info.emit(f"收到群文件: {filename}")

        elif action == "user_list":
            users = msg.get("users", [])
            self.on_users_update(users)

        elif action == "user_online":
            self.emitter.info.emit(f"{_from} 上线")

        elif action == "user_offline":
            self.emitter.info.emit(f"{_from} 下线")

        elif action == "server_close":
            self.emitter.info.emit("服务器关闭")
            self.close_chat_connection()

        # ✅ 刷新用户列表（更新未读数）
        users_list = self.build_users_list()
        self.on_users_update(users_list)
    def send_chat_message(self):
        if not self.sock:
            self.popUp("提示", "未连接到服务器", ok=False)
            return

        if not self.current_peer:
            self.popUp("提示", "请选择聊天对象", ok=False)
            return

        content = self.inputTextEdit.toPlainText().strip()
        if not content:
            return

        self.inputTextEdit.clear()
        timestamp = datetime.now()

        with self.sessions_lock:
            if self.current_peer == "__ALL__":
                req = {
                    "action": "public_msg",
                    "from": self.username,
                    "content": content,
                    "timestamp": timestamp.isoformat()
                }
                self.sessions["__ALL__"]["messages"].append((True, content, timestamp))
                self.refresh_message_view("__ALL__")
            else:
                # 确保用户名正确
                target_peer = self.current_peer.strip()
                print(f"[DEBUG] 发送私聊消息给: '{target_peer}', 当前用户名: '{self.username}'")

                req = {
                    "action": "private_msg",
                    "from": self.username,
                    "to": target_peer,
                    "content": content,
                    "timestamp": timestamp.isoformat()
                }

                # 检查目标用户是否在线
                with self.sessions_lock:
                    if target_peer in self.sessions:
                        is_online = self.sessions[target_peer].get("online", False)
                        print(f"[DEBUG] 目标用户 '{target_peer}' 在线状态: {is_online}")
                        if not is_online:
                            self.popUp("提示", f"用户 {target_peer} 当前不在线", ok=False)
                            return

                if self.current_peer not in self.sessions:
                    self.sessions[self.current_peer] = {
                        "messages": [],
                        "unread": 0,
                        "online": True,
                        "ip": ""
                    }
                self.sessions[self.current_peer]["messages"].append((True, content, timestamp))
                self.refresh_message_view(self.current_peer)

        try:
            with self.sock_lock:
                send_json(self.sock, req)
                print(f"[DEBUG] 消息已发送: {req}")
        except Exception as e:
            self.popUp("发送失败", str(e), ok=False)

    def send_file(self):
        if not self.sock:
            self.popUp("提示", "未连接到服务器", ok=False)
            return

        if not self.current_peer:
            self.popUp("提示", "请先选择聊天对象", ok=False)
            return

        file_path, _ = QFileDialog.getOpenFileName(self.window, "选择文件")
        if not file_path:
            return

        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        # =========================
        # 1. 本地立即显示
        # =========================
        file_msg = {
            "type": "file",
            "filename": filename,
            "path": file_path,
            "is_me": True,
            "time": datetime.now(),
            "from": self.username
        }

        with self.sessions_lock:
            if self.current_peer not in self.sessions:
                self.sessions[self.current_peer] = {
                    "messages": [],
                    "unread": 0,
                    "online": True,
                    "ip": ""
                }

            self.sessions[self.current_peer]["messages"].append(file_msg)
            self.refresh_message_view(self.current_peer)

        # =========================
        # 2. 判断：公聊 or 私聊
        # =========================
        if self.current_peer == "__ALL__":
            threading.Thread(
                target=self.send_public_file,
                args=(file_path, filename, file_size),
                daemon=True
            ).start()
        else:
            threading.Thread(
                target=self.send_private_file,
                args=(self.current_peer, file_path, filename, file_size),
                daemon=True
            ).start()

    def send_public_file(self, file_path, filename, file_size):
        try:
            with open(file_path, "rb") as f:
                data = base64.b64encode(f.read()).decode()

            req = {
                "action": "public_msg",
                "type": "file",
                "from": self.username,
                "filename": filename,
                "data": data
            }

            send_json(self.sock, req)

        except Exception as e:
            self.emitter.info.emit(f"公聊文件发送失败: {e}")
    def send_private_file(self, target_peer, file_path, filename, file_size):
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()

            encoded = base64.b64encode(file_data).decode()

            msg = {
                "action": "private_msg",
                "from": self.username,
                "to": target_peer,
                "type": "file",
                "filename": filename,
                "data": encoded
            }

            with self.sock_lock:
                send_json(self.sock, msg)

            self.emitter.info.emit(f"文件 {filename} 已发送")

        except Exception as e:
            self.emitter.info.emit(f"发送文件失败: {e}")

    def open_file(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)

        if not path:
            return

        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.warning(self.window, "无法打开", "文件不存在或已被删除")
    def add_time_item(self, time_text):
        item = QListWidgetItem()
        label = QLabel(time_text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("""
            QLabel {
                color: #888;
                background: transparent;
                padding: 4px;
            }
        """)
        item.setSizeHint(label.sizeHint())
        self.messageListWidget.addItem(item)
        self.messageListWidget.setItemWidget(item, label)


app = QApplication(sys.argv)
w = MainWindow()
w.window.show()
sys.exit(app.exec())