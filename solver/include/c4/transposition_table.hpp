#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "c4/position.hpp"

// Memory of search results, shared by every solver thread on the laptop (Pons parts 7, 11, 12).
//
// Each slot is one 64-bit word: the low 56 bits of the key in the upper part, and an 8-bit value
// in the lowest byte. The whole word is read and written in one step, so a thread never sees the
// key of one entry with the value of another. An empty slot is 0; stored values are never 0.
//
// Boards up to 56 key bits (8x7 and smaller) store the whole key, so two positions can never be
// mistaken for each other. Pons stores only 32 key bits to save memory; our slots are 8 bytes
// anyway, so the extra bits cost nothing. For bigger keys the number of slots, which is a prime,
// makes up the missing bits as in Pons part 11, as long as there are at least 2^(key bits - 56)
// slots. The constructor refuses smaller tables.
class TranspositionTable
{
public:
    static constexpr int KEY_BITS = Board::WIDTH * (Board::HEIGHT + 1);
    static constexpr int STORED_KEY_BITS = 56;
    static constexpr std::uint64_t STORED_KEY_MASK = (std::uint64_t{1} << STORED_KEY_BITS) - 1;

private:
    std::vector<std::atomic<std::uint64_t>> slots;

    std::size_t slotOf(std::uint64_t key) const
    {
        return static_cast<std::size_t>(key % slots.size());
    }

public:
    // Uses the largest prime number of slots that fits in `megabytes`.
    explicit TranspositionTable(std::size_t megabytes);

    // The smallest size in megabytes that keeps the guarantee above for this board size.
    static std::size_t minimumMegabytes();

    // Empties every slot.
    void clear();

    // Stores a value (1..255) for a key, replacing whatever the slot held.
    void put(std::uint64_t key, std::uint8_t value)
    {
        const std::uint64_t entry = ((key & STORED_KEY_MASK) << 8) | value;
        slots[slotOf(key)].store(entry, std::memory_order_relaxed);
    }

    // The value stored for a key, or 0 if there is none.
    int get(std::uint64_t key) const
    {
        const std::uint64_t entry = slots[slotOf(key)].load(std::memory_order_relaxed);
        if ((entry >> 8) != (key & STORED_KEY_MASK))
        {
            return 0;
        }
        return static_cast<int>(entry & 0xFFu);
    }

    std::size_t size() const
    {
        return slots.size();
    }
};
