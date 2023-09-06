# ROSBOT 2R
# using: Linux marcel 6.1.21-v8+ #1642 SMP PREEMPT Mon Apr  3 17:24:16 BST 2023 aarch64 GNU/Linux

sudo apt update
sudo apt upgrade

# misc
sudo apt-get install vim mc python3-venv python3-pip

# resize swap file
sudo dphys-swapfile swapoff
sudo vim /etc/dphys-swapfile
# set CONF_SWAPSIZE=2048
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
<!-- sudo swapoff /var/swap
sudo dd if=/dev/zero of=/var/swap bs=1M count=1024 oflag=append conv=notrun
sudo mkswap /var/swap
sudo swapon /var/swap -->

# install 8821cu-20210916 Realtek wifi driver for 0bda:c811
# 8821cu is usb2-only, doens't scan automatically, suffers from a lot of noise and apparently has BT turned on (all the time?)
# https://github.com/morrownr/USB-WiFi/blob/main/home/USB_WiFi_Chipsets.md
sudo apt install -y raspberrypi-kernel-headers build-essential bc dkms git
cd
git clone https://github.com/morrownr/8821cu-20210916.git
cd 8821cu-20210916/
sudo ./install-driver.sh
# >> don't edit config file & reboot

# docker
sudo apt-get install ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/raspbian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
# use debian bcs raspi repo broken
echo \
  "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# install docker
sudo systemctl enable docker.service
sudo systemctl enable containerd.service

sudo usermod -aG docker $USER
# >> log out & back in

# aiortc fork, TODO: cleanup and PR
cd
git clone git@github.com:PhantomCybernetics/aiortc.git
cd aiortc
git checkout origin/verbose

# aioice forked, TODO: cleanup and PR
cd
git clone git@github.com:PhantomCybernetics/aioice.git

# phntm bridge
cd
git clone git@github.com:PhantomCybernetics/phntm_bridge.git
ln -s phntm_bridge/rosbot2r-compose.yaml docker-compose.yaml

# build phntm-bridge:humble
cd
docker build -f phntm_bridge/dev.Dockerfile -t phntm-bridge:humble .

# pull from husarion
cd
docker compose pull rplidar joy2twist rosbot astra microros

# Husarion boot config from https://community.husarion.com/t/setup-raspberry-pi-4-with-core2-ros/1363
sudo echo "
# disable u-boot and boot directly to kernel
kernel=vmlinuz
initramfs initrd.img followkernel
# disable wifi
# dtoverlay=disable-wifi
dtoverlay=disable-bt # uart used for core2
# enable usb-C as host instead of power
#dtoverlay=dwc2,dr_mode=host #DON'T! this forces the shitty dwc2 driver that makes things laggy with more USB ios => pi's usb-c not working
# prevent overvolting
never_over_voltage=1
# speed up boot by disabling unnececery options
disable_poe_fan=1
disable_splash=1
boot_delay=0
# enable uart
enable_uart=1
" >> /boot/config.txt

otg_mode=1 # keep like this (doesn't apply to usb-c on pi)

sudo systemctl disable hciuart # uart used for core2

# remove forced serial baudrate
sudo sed -i "s/console=serial0,115200//g" /boot/firmware/cmdline.txt
# add fbcon=rotate:3 for screen rotation in text mode (90 deg ccw)

sudo reboot

sudo adduser $USER tty

# create simlink from /dev/ttyAMA0 to /dev/serial0
sudo -i
touch /etc/udev/rules.d/99_serial0.rules
echo 'KERNEL=="ttyAMA0", SYMLINK+="serial0"' >> /etc/udev/rules.d/99_serial0.rules
vim /etc/udev/rules.d/99-com.rules
# comment ttyAMA0 ttyAMA1 ttyS0 rules

udevadm control --reload-rules
udevadm trigger
# /dev/serial0 must point to ttyAMA0 for the flasher to work

sudo chown root.gpio /dev/gpiomem
sudo chmod g+rw /dev/gpiomem

# wlan0 will be service AP, wlan1 connects to wifi
# kinda works but alsko kinda doesn't with the Realtek 8821cu
# sudo mv /etc/wpa_supplicant/wpa_supplicant.conf /etc/wpa_supplicant/wpa_supplicant-wlan1.conf
# sudo apt-get -y install hostapd dnsmasq
# sudo echo 'denyinterfaces wlan0' >> /etc/dhcpcd.conf
# sudo vim /etc/network/interfaces
#        auto lo
#        iface lo inet loopback
#
#         auto wlan1
#         iface wlan1 inet dhcp
#
#         allow-hotplug wlan0
#         iface wlan0 inet static
#             address 10.0.1.1
#             netmask 255.255.0.0
#             network 10.0.1.0
#             broadcast 10.0.1.255
# sudo vim /etc/hostapd/hostapd.conf
#         interface=wlan0
#         driver=nl80211
#         ssid=MarcelOfTheseus
#         hw_mode=g
#         channel=6
#         ieee80211n=1
#         wmm_enabled=1
#         ht_capab=[HT40][SHORT-GI-20][DSSS_CCK-40]
#         macaddr_acl=0
#         auth_algs=1
#         ignore_broadcast_ssid=0
#         wpa=2
#         wpa_key_mgmt=WPA-PSK
#         wpa_passphrase=bijbolszewika
#         rsn_pairwise=CCMP
# sudo vim /etc/default/hostapd
#         DAEMON_CONF="/etc/hostapd/hostapd.conf"
# sudo vim /etc/dnsmasq.conf
#         interface=wlan0
#         listen-address=192.168.5.1
#         bind-interfaces
#         server=8.8.8.8
#         domain-needed
#         bogus-priv
#         dhcp-range=192.168.5.100,192.168.5.200,24h


# [install phntm-bridge docekrs]
# ...

# on first phntm-bridge launch do:
pip install -e /ros2_ws/aioice
pip install -e /ros2_ws/aiortc

# ?! numpy-1.21.5 -> numpy-1.25.2
pip uninstall numpy
pip install numpy

# tests:
v4l2-ctl --list-devices
libcamera-vid

# TODO:
#   CORE2 fw flashing ??
#   need to modprobe bcm2835_v4l2 ??
#   need to ./reload_devices.sh ??
# ressurces:
https://husarion.com/tutorials/howtostart/rosbot2r-quick-start/#flashing-the-firmware









