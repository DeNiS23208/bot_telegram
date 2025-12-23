#!/usr/bin/expect -f

set timeout 30
set server "178.72.153.64"
set user "root"
set password "uWawa8wwzCoa"

spawn ssh -o StrictHostKeyChecking=no $user@$server

expect {
    "password:" {
        send "$password\r"
        exp_continue
    }
    "yes/no" {
        send "yes\r"
        exp_continue
    }
    "# " {
        send "echo 'Connected successfully'\r"
    }
    timeout {
        puts "Connection timeout"
        exit 1
    }
}

interact

