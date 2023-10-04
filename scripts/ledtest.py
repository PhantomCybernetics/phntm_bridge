import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)

LEDS = [ 12, 16, 26 ]

PWM_1 = 13

for led in LEDS:
    GPIO.setup(led, GPIO.OUT, initial=GPIO.LOW)

# GPIO.setup(PWM_1, GPIO.OUT, initial=GPIO.LOW)
# pwm1 = GPIO.PWM(PWM_1, 10.0) #this spins the laser
# pwm1.start(100)

print(f'Yellow!')

delay = .3
phase = 0

try:
    while True:
        for i in range(len(LEDS)):
            GPIO.output(LEDS[i], phase==i)
        phase += 1
        if phase > len(LEDS)-1:
            phase = 0
        # GPIO.output(LED_0, True)
        # GPIO.output(LED_1, False)
        # print(f'ON')
        # time.sleep(delay)
        # GPIO.output(LED_0, False)
        # GPIO.output(LED_1, True)
        print(f'Beep {phase}')
        time.sleep(delay)
except:
    pass

# pwm1.stop()

GPIO.cleanup()


