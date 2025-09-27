import time
import pygame
from pynput.mouse import Controller, Button

pygame.init()
clock = pygame.time.Clock()
pygame.joystick.init()
mouse = Controller()
scroll_offset = 0.8
move_offset = 35 # 鼠标移动速度修正值，该值越大速度越快
AX_OFFSET = 1 # 摇杆偏移修正值，该值越大偏移越少，需大于等于1

# gamepad
joystick = pygame.joystick.Joystick(0)
joystick.init()

name = joystick.get_name()
print(f"Joystick name: {name}".format(name))

# 左中右的鼠标按键状态，用来将手柄按键和鼠标映射按键对应，避免刷新过快导致重复按下的情况
btnflg = [0, 0, 0, 0] 

# B: 1 映射右键, Y: 3 映射中键, X: 2 映射左键
btn_map = {2:Button.left, 3: Button.middle, 1: Button.right}

while True:
    for event in pygame.event.get(): # User did something
        pass
    #     if event.type == pygame.QUIT: # If user clicked close
    #         done=True # Flag that we are done so we exit this loop 

    axes = joystick.get_numaxes()
    btns = joystick.get_numbuttons()

    for i in range( btns ):
        # print(f"{i}, {joystick.get_button(i)}")
        btnst = joystick.get_button(i)

        # 按键通用模板
        def check_click(index, btnst):
            if index in btn_map.keys() and btnst == 1 and btnflg[index] == 0:
                btnflg[index] = 1
                mouse.press(btn_map[index])
            if index in btn_map.keys() and btnst == 0 and btnflg[index] == 1:
                btnflg[index] = 0
                mouse.release(btn_map[index])

        check_click(i, btnst)

    for i in range( axes ):
        move_dx = 0
        move_dy = 0
        scroll_dx = 0
        scroll_dy = 0
        scroll_delta = 0
        axis = joystick.get_axis( i )
        # if abs(axis - 0) > 0.01:
        #     print(f"Axis {i} value: {axis}")

        if i==0: # 0 左侧摇杆左右移动
            scroll_dx = round(axis, AX_OFFSET) * scroll_offset
        if i == 1: # 左侧摇杆上下移动
            scroll_dy = round(axis, AX_OFFSET) * scroll_offset
        if i == 2: # 右侧摇杆左右移动
            move_dx = round(axis, AX_OFFSET) * move_offset
        if i == 3: # 右侧摇杆上下移动
            move_dy = round(axis, AX_OFFSET) * move_offset
        if i == 4 : # 左扳机，常态-1, 完全按压为1
            _ = -i*round(axis, AX_OFFSET)*scroll_offset
        if i == 5 : # 右扳机，常态-1, 完全按压为1
            _ = -i*round(axis, AX_OFFSET)*scroll_offset


        if scroll_dx != 0 or scroll_dy != 0:
            if abs(scroll_dx) > abs(scroll_dy):
                mouse.scroll(scroll_dx, 0)
            else:
                mouse.scroll(0, scroll_dy)
        if move_dy != 0 or move_dx != 0:
            mouse.move(move_dx, move_dy)
        

    clock.tick(60) 
    # 采取60帧以平衡性能和耗电，如果需要更高的性能可以提高帧数，但是会增加耗电
    # 如果是电池较小的情况，可以考虑降低帧数
    # 25帧以下会导致鼠标出现明显拖影，建议最低30帧