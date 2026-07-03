import sys

from i18n import t


def _read_key() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        # TCSADRAIN: 기본값(TCSAFLUSH)은 대기 중인 입력을 버려서 빠른 연속 키 입력이 유실됨
        tty.setraw(fd, termios.TCSADRAIN)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # 화살표 키는 ESC [ A/B 시퀀스로 들어옴
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                return "\x1b[" + sys.stdin.read(1)
            return ch
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render(options, idx, first=False):
    if not first:
        sys.stdout.write(f"\x1b[{len(options)}A")  # 옵션 수만큼 커서를 위로
    for i, opt in enumerate(options):
        if i == idx:
            sys.stdout.write(f"\x1b[2K\x1b[36m  ❯ {opt}\x1b[0m\n")
        else:
            sys.stdout.write(f"\x1b[2K    {opt}\n")
    sys.stdout.flush()


def select_menu(title: str, options: list, default: int = 0) -> int:
    """화살표(↑/↓)로 이동, 엔터/스페이스로 선택. 선택된 인덱스를 반환.

    stdin이 TTY가 아니면(파이프 입력 등) 번호 입력 방식으로 폴백.
    """
    if not sys.stdin.isatty():
        print(title)
        for i, opt in enumerate(options, 1):
            print(f"  {i}) {opt}")
        while True:
            raw = input(t("u.choose", n=len(options))).strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            print(t("u.choosenum"))

    idx = default
    print(title + t("u.hint"))
    _render(options, idx, first=True)
    while True:
        key = _read_key()
        if key in ("\x1b[A", "k"):
            idx = (idx - 1) % len(options)
        elif key in ("\x1b[B", "j"):
            idx = (idx + 1) % len(options)
        elif key in ("\r", "\n", " "):
            print(f"→ {options[idx]}")
            return idx
        elif key == "\x03":  # Ctrl+C
            raise KeyboardInterrupt
        _render(options, idx)


def confirm(prompt: str) -> bool:
    from i18n import LANG
    options = ["예", "아니오"] if LANG == "ko" else ["Yes", "No"]
    return select_menu(prompt, options) == 0
