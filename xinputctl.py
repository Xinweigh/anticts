import json
import pygame
from pynput.mouse import Controller, Button

class XInput:
    def __init__(self):
        pygame.init()
        pygame.joystick.init()

        self.joystick = None
        self.mouse = Controller()
        self.clock = pygame.time.Clock()

        self.AX_OFFSET = 1  # 摇杆偏移修正值，该值越大偏移越少，需大于等于1
        self.move_offset = 10  # 鼠标移动速度修正值，该值越大速度越快
        self.scroll_spd = 0.2

        # 读取配置文件与按键映射
        with open("config.json", "r") as f:
            config = json.load(f)
            print(config.get("AX_OFFSET", 1))

        if pygame.joystick.get_count() > 0:
            self.joystick = pygame.joystick.Joystick(0)
            self.joystick.init()
            print(f"手柄初始化完成: {self.joystick.get_name()}")
        else:
            print("未检测到手柄")

    def joy_ctl(self):
        if not self.joystick:
            print("No joystick to read input from.")
            return

        while True:
            for event in pygame.event.get():
                if event.type == pygame.JOYAXISMOTION:
                    self.handle_axis_move(event.axis, event.value)
                    # print(f"Axis {event.axis} moved to {event.value}")
                elif event.type == pygame.JOYBUTTONDOWN:
                    self.handle_button_down(event.button)
                    print(f"Button {event.button} pressed")
                elif event.type == pygame.JOYBUTTONUP:
                    self.handle_button_up(event.button)
                    print(f"Button {event.button} released")
                elif event.type == pygame.JOYHATMOTION:
                    print(f"Hat {event.hat} moved to {event.value}")
            self.clock.tick(60)

    def handle_axis_move(self, axis, value):
        delta_list = [0, 0, 0, 0, 0, 0] # 存储每个轴的增量
        if axis == 0 or axis == 1: 
            # Axis 0 左侧摇杆左右移动
            # Axis 1 左侧摇杆上下移动
            mov_off = self.move_offset if abs(value) < 0.25 else self.move_offset * 4 * abs(value)
            delta_list[axis] = round(value, self.AX_OFFSET) * mov_off
        if axis == 2 or axis == 3: 
            # Axis 2 右侧摇杆左右移动
            # Axis 3 右侧摇杆上下移动
            delta_list[axis] = -axis * round(value, self.AX_OFFSET) * self.scroll_spd
        if axis == 4 or axis == 5: 
            # Axis 4 左侧扳机, Axis 5 右侧扳机，还没想好
            # TODO: 可写进json中作为自由适配项
            pass
        if delta_list[0] != 0 or delta_list[1] != 0:
            self.mouse.move(delta_list[0], delta_list[1])
        if delta_list[2] != 0 or delta_list[3] != 0:
            self.mouse.scroll(delta_list[2], delta_list[3])

    def handle_button_down(self, button):
        if button == 0: 
            # 鼠标左键按下
            self.mouse.press(Button.left)
        if button == 5: 
            # 鼠标右键按下
            self.mouse.press(Button.right)

    def handle_button_up(self, button):
        if button == 0:
            # 鼠标左键松开
            self.mouse.release(Button.left)
        if button == 5: 
            # 鼠标右键松开
            self.mouse.release(Button.right)


# Example usage
if __name__ == "__main__":
    gamepad = XInput()
    gamepad.joy_ctl()