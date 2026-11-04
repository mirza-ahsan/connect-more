// Unit tests for the transposition table (c4/transposition_table.hpp). Returns 0 when every check
// passes.

#include "c4/transposition_table.hpp"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace
{

int failures = 0;

void check(bool ok, const std::string& what)
{
    if (!ok)
    {
        std::cerr << "FAIL: " << what << '\n';
        failures++;
    }
}

bool isPrime(std::uint64_t n)
{
    if (n < 2)
    {
        return false;
    }
    for (std::uint64_t d = 2; d * d <= n; d++)
    {
        if (n % d == 0)
        {
            return false;
        }
    }
    return true;
}

// Largest key this board size can produce.
std::uint64_t maxKey()
{
    constexpr int bits = TranspositionTable::KEY_BITS;
    return bits >= 64 ? ~std::uint64_t{0} : (std::uint64_t{1} << bits) - 1;
}

// Value a thread writes for a key in the many-threads test: 1..255, worked out from the key.
std::uint8_t valueFor(std::uint64_t key)
{
    return static_cast<std::uint8_t>(key % 255 + 1);
}

std::size_t testSizeMb()
{
    return std::max<std::size_t>(16, TranspositionTable::minimumMegabytes());
}

void testSize()
{
    TranspositionTable table(testSizeMb());
    check(isPrime(table.size()), "number of slots is not prime");
    check(table.size() <= (testSizeMb() << 20) / 8, "table is bigger than asked");
    check(table.size() > (testSizeMb() << 20) / 8 - 1000, "table is much smaller than asked");

    // The size guarantee: slot number and stored key bits must tell every key apart.
    constexpr int missing = TranspositionTable::KEY_BITS - TranspositionTable::STORED_KEY_BITS;
    if constexpr (missing > 0)
    {
        check(table.size() > (std::uint64_t{1} << missing), "table too small for the key guarantee");
    }

    // Tables that are too small are refused.
    const std::size_t too_small_mb = TranspositionTable::minimumMegabytes() - 1;
    bool refused = false;
    try
    {
        TranspositionTable too_small(too_small_mb);
    }
    catch (const std::invalid_argument&)
    {
        refused = true;
    }
    check(refused, "a table of " + std::to_string(too_small_mb) + " MB was accepted");
}

void testStoreAndFind()
{
    TranspositionTable table(testSizeMb());
    const std::uint64_t size = table.size();

    check(table.get(12345) == 0, "empty table returns a value");

    table.put(12345, 7);
    check(table.get(12345) == 7, "stored value not found");

    // A key in the same slot is a different position and must not see the value.
    check(table.get(12345 + size) == 0, "different key in the same slot sees the value");

    // Keys in the same slot with the same low 32 bits (where the board's keys are big enough to
    // have them) are still told apart, because more than 32 key bits are stored.
    const std::uint64_t far = 12345 + (size << 32);
    if (far <= maxKey() && far > 12345)
    {
        check(table.get(far) == 0, "key with the same slot and low 32 bits sees the value");
    }

    // A new entry in the same slot replaces the old one.
    table.put(12345 + size, 9);
    check(table.get(12345 + size) == 9, "replacing entry not found");
    check(table.get(12345) == 0, "replaced entry still found");

    // Every value from 1 to 255 survives, including at the largest key.
    for (int value = 1; value <= 255; value++)
    {
        table.put(maxKey(), static_cast<std::uint8_t>(value));
        check(table.get(maxKey()) == value, "value " + std::to_string(value) + " not stored exactly");
    }

    // Key 0 is a real key (the empty board); its entry must differ from an empty slot.
    table.put(0, 1);
    check(table.get(0) == 1, "key 0 not stored");

    table.clear();
    check(table.get(0) == 0 && table.get(12345 + size) == 0, "clear left an entry behind");
}

// Many threads write and read the same small set of slots at once. A read must only ever return
// nothing or the value written for that exact key, never a mix of two entries.
void testManyThreads()
{
    TranspositionTable table(testSizeMb());
    const std::uint64_t size = table.size();
    const int threads = static_cast<int>(std::max(4u, std::thread::hardware_concurrency()));
    std::atomic<int> bad_reads{0};
    std::atomic<long long> found{0};

    std::vector<std::thread> workers;
    for (int t = 0; t < threads; t++)
    {
        workers.emplace_back(
            [&, t]
            {
                std::uint64_t x = 0x9E3779B97F4A7C15u * static_cast<std::uint64_t>(t + 1);
                for (int i = 0; i < 400000; i++)
                {
                    // 64 slots, 32 different keys per slot, all within the board's key range.
                    x = x * 6364136223846793005u + 1442695040888963407u;
                    const std::uint64_t slot = (x >> 20) % 64;
                    const std::uint64_t key = (slot + size * ((x >> 40) % 32)) & maxKey();
                    if ((x & 1) != 0)
                    {
                        table.put(key, valueFor(key));
                    }
                    else if (const int value = table.get(key); value != 0)
                    {
                        found++;
                        if (value != valueFor(key))
                        {
                            bad_reads++;
                        }
                    }
                }
            });
    }
    for (auto& worker : workers)
    {
        worker.join();
    }

    check(bad_reads == 0,
          std::to_string(bad_reads.load()) + " reads returned a value never stored for that key");
    check(found > 1000, "threads found too few entries for the test to mean anything");
}

}  // namespace

int main()
{
    std::cout << "Transposition table, board " << Board::WIDTH << "x" << Board::HEIGHT << ", " << testSizeMb()
              << " MB\n";

    testSize();
    testStoreAndFind();
    testManyThreads();

    if (failures != 0)
    {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "all checks passed\n";
    return 0;
}
