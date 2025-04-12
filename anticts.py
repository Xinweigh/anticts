import pygame
from pynput.mouse import Controller

pygame.init()
clock = pygame.time.Clock()
pygame.joystick.init()
mouse = Controller()
scroll_offset = 0.2
AX_OFFSET = 2 # 摇杆偏移修正值，该值越大偏移越少，需大于等于1

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
        if i==0 or i==3:
            continue
        axis = joystick.get_axis( i )
        # Axis 0: -1 left, 1 right
        # Axis 1: -1 up, 1 down
        # Axis 2: -1 up, 1 down
        # Axis 3: -1 left, 1 right
        # mouse scroll dy: -1 down, 1 up
        mouse.scroll(0, -i*round(axis, AX_OFFSET)*scroll_offset)

    clock.tick(20)