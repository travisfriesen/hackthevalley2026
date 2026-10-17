from gpiozero import DistanceSensor
from time import sleep

sensor = DistanceSensor(echo=17, trigger=4, max_distance=4)
sensor2 = DistanceSensor(echo=27, trigger=22, max_distance=4)
while True:
	try:
		print(f"sensor 1 {sensor.distance * 100} cm")
		print(f"sensor 2 {sensor2.distance * 100} cm")
	except Exception as e:
		print("Reading error:",e)
	sleep(0.2)
