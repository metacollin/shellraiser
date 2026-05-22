#!/bin/bash
# ============================================================================
#  shellraiser benchmark runner -- side-by-side comparison
#  Usage: bash examples/run_benchmark.sh [iterations]
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR" && pwd)"
BENCH="$SCRIPT_DIR/benchmark.sh"
BIN="$SCRIPT_DIR/benchmark_bin"
N="${1:-50000}"

echo ""
echo ""
echo "  shellraiser benchmark -- $N iterations per test"
echo ""

# Compile
echo ""
echo "Compiling..."
"$PROJECT_DIR/shellraiser" "$BENCH" -o "$BIN" 2>&1

# Run both, capture output
echo "Running bash..."
BASH_OUT=$(bash "$BENCH" "$N" 2>/dev/null)
echo "Running compiled..."
COMP_OUT=$("$BIN" "$N" 2>/dev/null)

# Parse and display side by side
echo ""
printf "%-36s  %9s  %9s  %8s\n" "Test" "Bash" "Compiled" "Speedup"
printf "%-36s  %9s  %9s  %8s\n" "----" "----" "--------" "-------"

paste <(echo "$BASH_OUT" | grep "ms  ") <(echo "$COMP_OUT" | grep "ms  ") | \
while IFS=$'\t' read -r bash_line comp_line; do
    # Extract name and time from each line
    name=$(echo "$bash_line" | sed 's/  *[0-9]*ms .*//')
    bash_ms=$(echo "$bash_line" | grep -o '[0-9]*ms' | head -1 | tr -d 'ms')
    comp_ms=$(echo "$comp_line" | grep -o '[0-9]*ms' | head -1 | tr -d 'ms')

    if [ -n "$bash_ms" ] && [ -n "$comp_ms" ] && [ "$comp_ms" -gt 0 ] 2>/dev/null; then
        # Calculate speedup (integer approximation x 10, then format)
        speedup_x10=$(( bash_ms * 10 / comp_ms ))
        whole=$((speedup_x10 / 10))
        frac=$((speedup_x10 % 10))
        printf "%-36s  %7sms  %7sms  %4s.%sx\n" "$name" "$bash_ms" "$comp_ms" "$whole" "$frac"
    elif [ -n "$bash_ms" ] && [ -n "$comp_ms" ]; then
        printf "%-36s  %7sms  %7sms      --\n" "$name" "$bash_ms" "$comp_ms"
    fi
done

echo ""
echo ""

# Cleanup
rm -f "$BIN" "${BIN}.c"