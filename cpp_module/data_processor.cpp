/**
 * data_processor.cpp — модуль обработки и агрегации данных.
 * Используется как тестовый проект для эксперимента с AI code review.
 */

#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <map>
#include <algorithm>
#include <numeric>
#include <cmath>
#include <stdexcept>
#include <memory>
#include <mutex>
#include <thread>

// ─── Data Structures ───────────────────────────────────────

struct Record {
    int id;
    std::string name;
    double value;
    std::string category;
    std::string timestamp;
};

struct Statistics {
    double mean;
    double median;
    double std_dev;
    double min_val;
    double max_val;
    size_t count;
};

// ─── CSV Parser ────────────────────────────────────────────

class CsvParser {
public:
    explicit CsvParser(const std::string& filepath)
        : filepath_(filepath) {}

    std::vector<Record> parse() {
        std::ifstream file(filepath_);
        if (!file.is_open()) {
            throw std::runtime_error("Cannot open file: " + filepath_);
        }

        std::vector<Record> records;
        std::string line;

        // Skip header
        std::getline(file, line);

        while (std::getline(file, line)) {
            if (line.empty()) continue;

            Record rec = parse_line(line);
            records.push_back(rec);
        }

        file.close();
        return records;
    }

private:
    std::string filepath_;

    Record parse_line(const std::string& line) {
        std::stringstream ss(line);
        std::string token;
        Record rec;

        std::getline(ss, token, ',');
        rec.id = std::stoi(token);

        std::getline(ss, rec.name, ',');

        std::getline(ss, token, ',');
        rec.value = std::stod(token);

        std::getline(ss, rec.category, ',');
        std::getline(ss, rec.timestamp, ',');

        return rec;
    }
};

// ─── Statistics Calculator ─────────────────────────────────

class StatsCalculator {
public:
    static Statistics compute(const std::vector<double>& values) {
        if (values.empty()) {
            throw std::invalid_argument("Cannot compute statistics on empty dataset");
        }

        Statistics stats;
        stats.count = values.size();

        // Mean
        stats.mean = std::accumulate(values.begin(), values.end(), 0.0) / stats.count;

        // Min/Max
        auto [min_it, max_it] = std::minmax_element(values.begin(), values.end());
        stats.min_val = *min_it;
        stats.max_val = *max_it;

        // Median
        std::vector<double> sorted = values;
        std::sort(sorted.begin(), sorted.end());
        if (stats.count % 2 == 0) {
            stats.median = (sorted[stats.count / 2 - 1] + sorted[stats.count / 2]) / 2.0;
        } else {
            stats.median = sorted[stats.count / 2];
        }

        // Standard deviation
        double sq_sum = 0.0;
        for (const auto& v : values) {
            sq_sum += (v - stats.mean) * (v - stats.mean);
        }
        stats.std_dev = std::sqrt(sq_sum / stats.count);

        return stats;
    }
};

// ─── Data Aggregator ───────────────────────────────────────

class DataAggregator {
public:
    using GroupedData = std::map<std::string, std::vector<double>>;

    static GroupedData group_by_category(const std::vector<Record>& records) {
        GroupedData groups;
        for (const auto& rec : records) {
            groups[rec.category].push_back(rec.value);
        }
        return groups;
    }

    static std::map<std::string, Statistics> aggregate(const std::vector<Record>& records) {
        auto groups = group_by_category(records);
        std::map<std::string, Statistics> result;

        for (const auto& [category, values] : groups) {
            result[category] = StatsCalculator::compute(values);
        }

        return result;
    }
};

// ─── Report Generator ──────────────────────────────────────

class ReportGenerator {
public:
    static void generate_text_report(const std::map<std::string, Statistics>& data,
                                     const std::string& output_path) {
        std::ofstream out(output_path);
        if (!out.is_open()) {
            throw std::runtime_error("Cannot create report file: " + output_path);
        }

        out << "=== Data Analysis Report ===" << std::endl;
        out << std::endl;

        for (const auto& [category, stats] : data) {
            out << "Category: " << category << std::endl;
            out << "  Count:    " << stats.count << std::endl;
            out << "  Mean:     " << stats.mean << std::endl;
            out << "  Median:   " << stats.median << std::endl;
            out << "  Std Dev:  " << stats.std_dev << std::endl;
            out << "  Min:      " << stats.min_val << std::endl;
            out << "  Max:      " << stats.max_val << std::endl;
            out << std::endl;
        }

        out.close();
    }

    static void generate_csv_report(const std::map<std::string, Statistics>& data,
                                    const std::string& output_path) {
        std::ofstream out(output_path);
        if (!out.is_open()) {
            throw std::runtime_error("Cannot create report file: " + output_path);
        }

        out << "category,count,mean,median,std_dev,min,max" << std::endl;

        for (const auto& [category, stats] : data) {
            out << category << ","
                << stats.count << ","
                << stats.mean << ","
                << stats.median << ","
                << stats.std_dev << ","
                << stats.min_val << ","
                << stats.max_val << std::endl;
        }

        out.close();
    }
};

// ─── Concurrent Processor ──────────────────────────────────

class ConcurrentProcessor {
public:
    ConcurrentProcessor(size_t num_threads = 4)
        : num_threads_(num_threads) {}

    std::map<std::string, Statistics> process(const std::vector<Record>& records) {
        auto groups = DataAggregator::group_by_category(records);
        std::map<std::string, Statistics> results;
        std::mutex results_mutex;

        std::vector<std::thread> threads;

        for (const auto& [category, values] : groups) {
            threads.emplace_back([&, category, values]() {
                auto stats = StatsCalculator::compute(values);
                std::lock_guard<std::mutex> lock(results_mutex);
                results[category] = stats;
            });
        }

        for (auto& t : threads) {
            t.join();
        }

        return results;
    }

private:
    size_t num_threads_;
};

// ─── Main ──────────────────────────────────────────────────

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <input.csv> [output_report]" << std::endl;
        return 1;
    }

    std::string input_file = argv[1];
    std::string output_file = (argc >= 3) ? argv[2] : "report.txt";

    try {
        CsvParser parser(input_file);
        auto records = parser.parse();

        std::cout << "Parsed " << records.size() << " records." << std::endl;

        auto aggregated = DataAggregator::aggregate(records);
        ReportGenerator::generate_text_report(aggregated, output_file);

        std::cout << "Report saved to " << output_file << std::endl;

        // Print summary
        for (const auto& [cat, stats] : aggregated) {
            std::cout << cat << ": mean=" << stats.mean
                      << ", count=" << stats.count << std::endl;
        }

    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
