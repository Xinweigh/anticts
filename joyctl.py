import pygame
import pynput
import time

class JoyCtl:
    def __init__(self):
        pygame.init()
        pygame.joystick.init()
        self.joystick = None
        if pygame.joystick.get_count() > 0:
            # 仅选用第一个手柄
            self.joystick = pygame.joystick.Joystick(0)
            self.joystick.init()
        self.keyboard = pynput.keyboard.Controller()
        self.mouse = pynput.mouse.Controller()

    def process_events(self):
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                self.handle_button_down(event.button)
            elif event.type == pygame.JOYBUTTONUP:
                self.handle_button_up(event.button)
            elif event.type == pygame.JOYAXISMOTION:
                self.handle_axis_motion(event.axis, event.value)

    def handle_button_down(self, button):
        # Map joystick buttons to keyboard keys or mouse actions
        pass

    def handle_button_up(self, button):
        # Handle button release actions
        pass

    def handle_axis_motion(self, axis, value):
        # Map joystick axis movement to mouse movement or other actions
        pass

    def run(self):
        try:
            while True:
                self.process_events()
                time.sleep(0.01)
        except KeyboardInterrupt:
            pygame.quit()