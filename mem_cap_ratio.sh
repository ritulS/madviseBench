#!/bin/bash
# ratio_size.sh — compute N × effective memory limit (cgroup or physical)

r="$1"
rel="$(awk -F: '$1==0{print $3}' /proc/self/cgroup)"
cg="/sys/fs/cgroup${rel}"
max="$(cat "$cg/memory.max" 2>/dev/null || echo max)"
if [ "$max" = "max" ] || [ -z "$max" ]; then
  max="$(awk '/MemTotal/ {print $2*1024}' /proc/meminfo)"
fi
echo "$(echo "$r * $max" | bc -l | awk '{printf "%d", $1}')"
