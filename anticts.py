import pygame
from pynput.mouse import Controller

pygame.init()
clock = pygame.time.Clock()
pygame.joystick.init()
mouse = Controller()
scroll_offset = 0.2
move_offset = 35 # 鼠标移动速度修正值，该值越大速度越快
AX_OFFSET = 1 # 摇杆偏移修正值，该值越大偏移越少，需大于等于1

# gamepad
joystick = pygame.joystick.Joystick(0)
joystick.init()

name = joystick.get_name()
print(f"Joystick name: {name}".format(name))

while True:
    for event in pygame.event.get(): # User did something
        pass
    #     if event.type == pygame.QUIT: # If user clicked close
    #         done=True # Flag that we are done so we exit this loop 

    axes = joystick.get_numaxes()
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
            move_dx = round(axis, AX_OFFSET) * move_offset
        if i == 1 or i == 2: # 1,2 左侧摇杆上下移动
            move_dy = round(axis, AX_OFFSET) * move_offset
        if i==3: # 3 右侧摇杆左右移动
            scroll_dx = -i*round(axis, AX_OFFSET)*scroll_offset
        if i == 4 or i == 5: # 4,5 右侧摇杆上下移动
            scroll_dy = -i*round(axis, AX_OFFSET)*scroll_offset

        # Axis 0: -1 left, 1 right
        # Axis 1: -1 up, 1 down
        # Axis 2: -1 up, 1 down
        # Axis 3: -1 left, 1 right
        # mouse scroll dy: -1 down, 1 up
        mouse.scroll(scroll_dx, scroll_dy)
        if move_dy != 0 or move_dx != 0:
            # print(f"move_dx: {move_dx}, move_dy: {move_dy}")
            mouse.move(move_dx, move_dy)
        

    clock.tick(60) 
    # 采取60帧以平衡性能和耗电，如果需要更高的性能可以提高帧数，但是会增加耗电
    # 如果是电池较小的情况，可以考虑降低帧数
    # 25帧以下会导致鼠标出现明显拖影，建议最低30帧