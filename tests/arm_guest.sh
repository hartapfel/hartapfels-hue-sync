#!/bin/sh
# PID 1 inside the disposable ARM VM. All traffic stays on guest loopback.
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
mount -t proc proc /proc
mount -t devtmpfs devtmpfs /dev
ip link set lo up
cd /fixture

wait_port() {
    for i in $(seq 1 120); do
        grep -qi ':4BC9 .* 0A ' /proc/net/tcp && return 0
        sleep .05
    done
    return 1
}

test_package() {
    LD_LIBRARY_PATH=/sdk:/app /sdk/ld-linux.so.3 --list /app/c9-sync >sdk.list 2>sdk.err || return 1
    grep -q '/sdk/libc.so.6' sdk.list && grep -q '/app/libc9_hue_stream.so' sdk.list || return 1
    /app/c9-sync </dev/null 2>invalid.err
    [ "$?" = 2 ] && grep -q 'Invalid screen sync session' invalid.err || return 1
    /app/c9-sync --measure-hdr-chart >chart.out 2>chart.err &
    child=$!
    wait_port || return 1
    cat register chart chart chart chart chart chart chart chart | nc -w 2 127.0.0.1 19401 >acks
    wait "$child" && [ -s acks ] || return 1
    cat chart.out

    mkfifo peer.in
    exec 3<>peer.in
    openssl s_server -4 -dtls1_2 -quiet -nocert -naccept 1 -cipher PSK-AES128-GCM-SHA256 \
        -psk 00112233445566778899aabbccddeeff -psk_identity c9-worker-test -accept 127.0.0.1:2100 \
        <&3 >packets 2>peer.err &
    peer=$!
    for i in $(seq 1 100); do
        grep -qi ':0834 ' /proc/net/udp && break
        sleep .05
    done
    /app/c9-sync <descriptor >session.out 2>session.err &
    child=$!
    wait_port || return 1
    { cat register; for i in $(seq 1 14); do cat split; sleep .05; done
      for i in $(seq 1 14); do cat white; sleep .05; done; } | nc -w 2 127.0.0.1 19401 >acks
    wait "$child"
    [ "$?" = 1 ] && grep -q 'PicCap input disconnected' session.err || return 1
    grep -qx streaming session.out && [ -s acks ] || return 1
    wait "$peer" || return 1
    echo C9-PACKETS-BEGIN
    base64 packets
    echo C9-PACKETS-END
}

# Give the worker a service-like parent. It treats adoption by PID 1 as an
# orphaned service invocation and exits before reading a session descriptor.
if (test_package; exit "$?"); then
    echo C9-ARM-PASSED
else
    echo C9-ARM-FAILED
    for f in *.err; do cat "$f"; done
fi
poweroff -f
