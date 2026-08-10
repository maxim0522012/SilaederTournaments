@echo off
netsh advfirewall firewall add rule name="School Table Tennis Server" dir=in action=allow protocol=TCP localport=5000 profile=private
pause
