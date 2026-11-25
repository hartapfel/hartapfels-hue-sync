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
    # The guest has no LG display libraries. The failure must name a firmware
    # dependency, proving the installed worker found its own real DILE backend.
    /app/c9-sync --measure-hdr-chart direct >missing.out 2>missing.err
    [ "$?" = 1 ] && grep -Eq 'C9 video backend: lib(PmLogLib|dile_vt)\.so' missing.err || return 1
    cp /app/c9-sync /app/libc9_hue_stream.so /capture-fixture/ || return 1
    /capture-fixture/c9-sync --measure-hdr-chart direct >direct.out 2>direct.err || return 1
    if [ -f desktop-ticks ]; then
        grep '^Desktop ticks ' direct.err || return 1
    fi
    cat direct.out
    /app/c9-sync --measure-hdr-chart >chart.out 2>chart.err &
    child=$!
    wait_port || return 1
    cat register chart chart chart chart chart chart chart chart | nc -w 2 127.0.0.1 19401 >acks
    wait "$child" && [ -s acks ] || return 1
    cat chart.out

    test_stream split || return 1
    if [ -f chart-descriptor ]; then test_stream chart || return 1; fi
    return 0
}

feed_frames() {
    if [ "$1" = chart ]; then
        cat register chart
        for i in $(seq 1 100); do
            grep -qx streaming session.out && break
            sleep .05
        done
        # Keep the same source for at least 16 seconds after the first
        # output, below the 101 new observations required to change the crop.
        for i in $(seq 1 80); do cat chart; sleep .2; done
    else
        cat register split
        for i in $(seq 1 100); do
            grep -qx streaming session.out && break
            sleep .05; cat split
        done
        for i in $(seq 1 14); do cat split; sleep .05; done
        for i in $(seq 1 14); do cat white; sleep .05; done
    fi
}

test_stream() {
    descriptor=descriptor
    if [ "$1" = chart ]; then descriptor=chart-descriptor; fi
    mkfifo peer.in worker.in
    exec 4<>worker.in
    /app/c9-sync --wait-for-bridge <worker.in >session.out 2>session.err &
    child=$!
    cat "$descriptor" >&4
    wait_port && [ ! -s session.out ] || return 1
    feed_frames "$1" | nc -w 2 127.0.0.1 19401 >acks &
    for i in $(seq 1 100); do grep -qx video-ready session.out && break; sleep .05; done
    [ "$(cat session.out)" = video-ready ] || return 1
    # Start the bridge peer only after the worker has processed a real frame.
    exec 3<>peer.in
    openssl s_server -4 -dtls1_2 -quiet -nocert -naccept 1 -cipher PSK-AES128-GCM-SHA256 \
        -psk 00112233445566778899aabbccddeeff -psk_identity c9-worker-test -accept 127.0.0.1:2100 \
        <&3 >packets 2>peer.err &
    peer=$!
    for i in $(seq 1 100); do
        grep -qi ':0834 ' /proc/net/udp && break
        sleep .05
    done
    printf '\001' >&4
    wait "$child"
    [ "$?" = 1 ] && grep -q 'PicCap input disconnected' session.err || return 1
    [ "$(cat session.out)" = "$(printf 'video-ready\nstreaming')" ] && [ -s acks ] || return 1
    wait "$peer" || return 1
    if [ "$1" = chart ]; then
        echo "C9-CHART-STREAM $(wc -c <packets)"
        # One HueStream header and 14 RGB16 channel records per frame.
        head -c 150 packets | base64
        tail -c 150 packets | base64
        echo C9-CHART-END
    else
        echo C9-PACKETS-BEGIN
        base64 packets
        echo C9-PACKETS-END
    fi
    exec 3>&- 4>&-
    rm peer.in worker.in
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
