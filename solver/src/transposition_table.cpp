#include "c4/transposition_table.hpp"

#include <stdexcept>
#include <string>

namespace
{

bool isPrime(std::uint64_t n)
{
    if (n < 2)
    {
        return false;
    }
    if (n % 2 == 0)
    {
        return n == 2;
    }
    for (std::uint64_t d = 3; d * d <= n; d += 2)
    {
        if (n % d == 0)
        {
            return false;
        }
    }
    return true;
}

// Slots needed so that the slot number and the stored key bits together tell every key apart.
std::uint64_t minimumSlots()
{
    constexpr int bits = TranspositionTable::KEY_BITS;
    constexpr int missing = bits - TranspositionTable::STORED_KEY_BITS;
    if constexpr (missing > 0)
    {
        return std::uint64_t{1} << missing;
    }
    return 1;
}

std::size_t primeSlots(std::size_t megabytes)
{
    std::uint64_t n = (std::uint64_t{megabytes} << 20) / sizeof(std::uint64_t);
    while (n > 1 && !isPrime(n))
    {
        n--;
    }
    if (n <= minimumSlots())
    {
        throw std::invalid_argument("transposition table of " + std::to_string(megabytes) +
                                    " MB is too small for this board; use at least " +
                                    std::to_string(TranspositionTable::minimumMegabytes()) + " MB");
    }
    return static_cast<std::size_t>(n);
}

}  // namespace

TranspositionTable::TranspositionTable(std::size_t megabytes) : slots(primeSlots(megabytes)) {}

std::size_t TranspositionTable::minimumMegabytes()
{
    return static_cast<std::size_t>((minimumSlots() * sizeof(std::uint64_t)) >> 20) + 1;
}

void TranspositionTable::clear()
{
    for (auto& slot : slots)
    {
        slot.store(0, std::memory_order_relaxed);
    }
}
