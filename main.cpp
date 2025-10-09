#include <iostream>
#include <vector>
#include <string>
#include <cmath>        
#include <cstdint>      
#include <cstring>      
#include <fstream> 
#include <iomanip>
#include <algorithm>
#include <random>
#include <fstream>
#include <sstream>
#include <chrono>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <sys/resource.h>

///////////
// sudo systemd-run --scope -p MemoryMax=3G bash
// g++ -O2 -std=c++17 -Wall -Wextra -o madvbench main.cpp
// fallocate -l 2G test.dat

// ./madvbench --file test.dat --size-ratio 0.75 --pattern rand --madv rand --repeat 3 --temp cold
//////////

// ---------- parsing helpers ----------
static std::string get_flag_value (int argc, char** argv, const std::string& flag,
                                     const std::string& def=""){
    for (int i = 1; i < argc - 1; i++) {
        if (std::string(argv[i]) == flag) return argv[i+1];
    }
    return def;
}

// ---------- stats helpers ----------
static double percentile(std::vector<double> v, double per) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    double pos = (per/100.0) * (v.size() - 1);
    size_t lo = static_cast<size_t>(pos);
    double frac = pos - lo;
    if (lo >= v.size()-1) return v.back();
    return v[lo] + frac * (v[lo+1] - v[lo]);
}

static double median(std::vector<double> v) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    size_t n = v.size();
    if (n % 2 == 1){
        return v[n/2];
    } else {
        return 0.5 * (v[n/2 - 1] + v[n/2]);
    }
}

// ---------- mapping helper ----------
// mmap the file and apply madvice (seq/rand/none).
static void* map_and_advise(int fd, size_t map_len, std::string madv_flag){
    void* base = mmap(nullptr, map_len, PROT_READ, MAP_SHARED, fd, 0);
    if (base == MAP_FAILED) {perror("mmap"); return nullptr;}
    if (madv_flag == "seq") {
        if (madvise(base, map_len, MADV_SEQUENTIAL) != 0){
            perror("madvise(MADV_SEQUENTIAL)");
        }
    } else if (madv_flag == "rand") {
        if (madvise(base, map_len, MADV_RANDOM) != 0){
            perror("madvise(MADV_RANDOM)");
        }
    }
    return base;
}

// ---------- helpers ----------
static size_t total_ram_bytes() {
    long pages = sysconf(_SC_PHYS_PAGES);
    long psize = sysconf(_SC_PAGESIZE);
    if (pages <= 0 || psize <= 0) return 0;
    return static_cast<size_t>(pages) * static_cast<size_t>(psize);
}


static size_t effective_ram_limit_bytes() {
    // default: physical RAM
    size_t phys = total_ram_bytes();

    // try cgroup v2 memory.max (unified hierarchy)
    std::ifstream cg("/proc/self/cgroup");
    std::string line, rel;
    while (std::getline(cg, line)) {
        auto pos = line.find("::/");
        if (pos != std::string::npos) {
            rel = line.substr(pos + 2);
            break;
        }
    }
    if (!rel.empty()) {
        std::string path = "/sys/fs/cgroup" + rel + "/memory.max";
        std::ifstream f(path);
        if (f) {
            std::string val; f >> val;
            if (val != "max") {
                unsigned long long bytes = 0;
                std::istringstream(val) >> bytes;
                if (bytes > 0) return bytes;
            }
        }
    }

    return phys;
}

int main(int argc, char** argv) {

    if (argc < 3) {
        std::cerr << "Usage:\n  " << argv[0]
            << " --file PATH --size-ratio <float> --pattern {seq|rand|stride:N}\n"
            << " --madv {none|seq|rand} [--seed N] [--repeat N] [--temp {hot|cold|none}]\n";
        return 1;
    }

    std::string file      = get_flag_value(argc, argv, "--file");
    // std::string size_b      = get_flag_value(argc, argv, "--size");
    std::string size_ratio  = get_flag_value(argc, argv, "--size-ratio");
    std::string pattern   = get_flag_value(argc, argv, "--pattern");
    std::string madv_flag = get_flag_value(argc, argv, "--madv");
    std::string seed      = get_flag_value(argc, argv, "--seed", "1");
    std::string repeat    = get_flag_value(argc, argv, "--repeat", "5");
    std::string temp_mode = get_flag_value(argc, argv, "--temp", "none"); // hot/cold/none
    std::string csv = get_flag_value(argc, argv, "--csv", ""); // empty = no CSV

    bool csv_mode = !csv.empty();
    std::ostream& log = csv_mode ? std::cerr : std::cout;

    if (size_ratio.empty()) {
        std::cerr << "ERROR: missing --size-ratio <float> (e.g., 0.75, 1.0, 1.5)\n";
        return 2;
    }
    if (!(pattern.rfind("stride:", 0) == 0 || pattern == "seq" || pattern == "rand")){
        std::cerr << "ERROR: PATTERN must be 'seq', 'rand' or 'stride<N>'\n";
        return 2;
    }
    if (!(madv_flag == "seq" || madv_flag == "rand" || madv_flag == "none")){
        std::cerr << "ERROR: MADVISE_FLAG must be 'seq' or 'rand'\n";
        return 2;
    }

    
    log << "\n---- EXP DETAILS ----" << "\n";
    log << "file = " << file << ", ";
    // log << "size_b = " << size_b << ",";
    log << "size_ratio = " << size_ratio << ",";
    log << "pattern = " << pattern << ", ";
    log << "madv_flag = " << madv_flag << ", ";
    log << "seed = " << seed << ", ";
    log << "temperature_mode=" << temp_mode << ",";
    log << "repeat =" << repeat << "\n\n";

    int fd = open(file.c_str(), O_RDONLY | O_NOATIME);
    if (fd < 0){
        perror("open"); return 1;
    }

    struct stat st{};
    if (fstat(fd, &st) != 0){
        perror("fstat"); close(fd); return 1;
    }

    size_t file_size = static_cast<size_t>(st.st_size);
    if (file_size == 0) {
        std::cerr << "ERROR: file is empty\n";
        close(fd); return 2;
    }

    // size_t map_len = req_size <= file_size ? req_size : file_size;
    //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    size_t map_len = 0;
    unsigned long long req_size = 0;
    // if (!size_b.empty()) {
    //     try {req_size = std::stoull(size_b);}
    //     catch (...) {
    //         std::cerr << "ERROR: --size must be an integer num of bytes\n";
    //         return 2;
    //     }
    //     map_len = static_cast<size_t>(std::min<unsigned long long>(req_size, file_size));
    //     size_t avail = effective_ram_limit_bytes();
    //     std::cout << "--- Memory Pressure ---\n"
    //       << "  Available RAM limit: " << avail
    //       << " bytes (" << (avail / (1024.0*1024.0)) << " MiB)"
    //       << "Final map_len: " << map_len
    //       << " bytes (" << (map_len / (1024.0*1024.0)) << " MiB)"
    //       << ", File size: " << file_size
    //       << " bytes (" << (file_size / (1024.0*1024.0)) << " MiB)\n"
    //       << std::endl;
    // } 
    
    double r = 0.0;
    try { r = std::stod(size_ratio);}
    catch (...) {
        std::cerr << "ERROR: --size-ratio must be a positiv float 0.75-1.50\n";
        return 2;
    }
    const size_t avail_dram = effective_ram_limit_bytes();
    if (avail_dram == 0){
        std::cerr << "could not detect RAM\n"; close(fd);
        return 2;
    }

    unsigned long long target_len = static_cast<unsigned long long>(r * static_cast<double>(avail_dram));
    map_len = static_cast<size_t>(std::min<unsigned long long>(target_len, file_size));
    log << "Size ratio: " << size_ratio << ", " << "Avail Dram: "
                << avail_dram << ", " << "target_size: " << target_len << ", "
                << "file_size: " << file_size <<"\n\n";
    
    const long pagesz = sysconf(_SC_PAGESIZE);
    size_t file_clip = (file_size / static_cast<size_t>(pagesz)) * static_cast<size_t>(pagesz);
    map_len = (map_len / static_cast<size_t>(pagesz)) * static_cast<size_t>(pagesz);
    map_len = std::min(map_len, file_clip);

    if (map_len == 0) {
        std::cerr << "ERROR: mapping length resolved to 0 bytes\n";
        close(fd);
        return 2;
    }

    log << "--- Memory Pressure ---\n"
              << " Effective RAM limit: " << avail_dram
              << " bytes (" << (avail_dram / (1024.0 * 1024.0 * 1024.0)) << " GiB)\n"
              << " size_ratio: " << r << "\n"
              << " target_len__size: " << target_len
              << " bytes (" << (target_len / (1024.0 * 1024.0)) << " MiB)\n"
              << " file_size: " << file_size
              << " bytes (" << (file_size / (1024.0 * 1024.0)) << " MiB)\n"
              << " final map_len: " << map_len
              << " bytes (" << (map_len / (1024.0 * 1024.0)) << " MiB)\n\n";
    //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    // -----------------------------
    // Initial map & advise (first run)
    // -----------------------------
    void* base = map_and_advise(fd, map_len, madv_flag);
    if (base == nullptr) {perror("mmap"); close(fd); return 1;}

    // ------------------------------------------
    // Page size & access order (seq/rand)
    // ------------------------------------------
    const size_t npages = (map_len + pagesz - 1) / pagesz;
    long long stride_val = -1;
    std::vector<size_t> order(npages);
    for (size_t i = 0; i < npages; i++) order[i] = i; // For Seq access
    if (pattern == "rand") { // For Rand access
        std::mt19937_64 rng{std::stoull(seed)};
        std::shuffle(order.begin(), order.end(), rng);
    } else if (pattern.rfind("stride:", 0) == 0) {
        std::string step = pattern.substr(std::string("stride:").size());
        size_t stride = 0;
        try {
            stride = std::stoull(step);
        } catch (...) {
            std::cerr << "ERROR: Invalid stride "<< step
                      << "integer needed (pages)\n";
            return 2;
        }
        if (stride == 0) stride = 1;
        stride_val = static_cast<long long>(stride);
        // Gen stride access order
        std::vector<size_t> tmp; tmp.reserve(npages);
        for (size_t start = 0; start < stride; start++) {
            for (size_t j = start; j < npages; j += stride) tmp.push_back(j);
        }
        order.swap(tmp);
    }

    // -----------------------------
    // MEASURE: TIME & FAULTS
    // -----------------------------
    volatile uint8_t* p = static_cast<volatile uint8_t*>(base);
    uint8_t sink = 0;

    size_t repeat_count = std::stoull(repeat);
    std::vector<double> times;  times.reserve(repeat_count);
    std::vector<double> thrpts; thrpts.reserve(repeat_count);
    std::vector<double> minflts; minflts.reserve(repeat_count);
    std::vector<double> majflts; majflts.reserve(repeat_count);

    bool cold = (temp_mode == "cold");
    bool hot = (temp_mode == "hot");

    if (!csv.empty()) {
        std::cout <<
        "file,size_ratio,pattern,stride_pages,madv,temp,repeat_idx,time_s,"
        "throughput_mibps,minflt,majflt,npages,pagesz,map_len,file_size,"
        "avail_ram,seed\n";
    }
    // RUN LOOP
    for (int run = 0; run < repeat_count; run++) {

        if (cold) {
            // Clearing Page Cache for this file
            int pf = posix_fadvise(fd, 0, map_len, POSIX_FADV_DONTNEED);
            if (pf != 0) {
                std::cerr << "posix_fadvise(DONTNEED) failed: " << strerror(pf) << "\n";
            }
            // Drop PTEs by unmapping, then remap and re-apply advice
            munmap(base, map_len);
            base = map_and_advise(fd, map_len, madv_flag);
            if (base == nullptr) {perror("mmap"); close(fd); return 1;}
            p = static_cast<volatile uint8_t*>(base);

        } else if (hot && run == 0) {
            // first run only; keep mapping & cache warm for all subsequent
            int pf2 = posix_fadvise(fd, 0, map_len, POSIX_FADV_WILLNEED);
            if (pf2 != 0){
                std::cerr << "posix_fadvise(WILLNEED) failed: " << strerror(pf2) << "\n";
            }
        }

        rusage ru_before{};
        if (getrusage(RUSAGE_SELF, &ru_before) != 0){
            perror("getrusage(before)");
        }

        auto t0 = std::chrono::steady_clock::now();
        for (size_t idx : order) {
            size_t off = idx * pagesz;
            if (off >= map_len) continue;
            sink ^= p[off];
            // for (size_t i = 0; i < pagesz; i++)  // touch the whole page
            //     sink ^= p[off + i];
        }
        auto t1 = std::chrono::steady_clock::now();
        (void)sink;

        rusage ru_after{};
        if (getrusage(RUSAGE_SELF, &ru_after)){
            perror("getrusage(after)");
        }
        long minflt_delta = ru_after.ru_minflt - ru_before.ru_minflt;
        long majflt_delta = ru_after.ru_majflt - ru_before.ru_majflt;

        double sec = std::chrono::duration<double>(t1-t0).count();
        double mib = ((double)npages/1024) * ((double)pagesz/1024);
        double mibps = mib / sec;
        
        bool should_record = !(hot && run == 0); // skip first run if hot
        if (should_record) {
            times.push_back(sec);
            thrpts.push_back(mibps);
            minflts.push_back(minflt_delta);
            majflts.push_back(majflt_delta);
            if (!csv.empty()) {
                std::cout.setf(std::ios::fixed); 
                std::cout << std::setprecision(6)
                        << file << ","
                        << r << ","
                        << pattern << ","
                        << stride_val << ","
                        << madv_flag << ","
                        << temp_mode << ","
                        << run << ","
                        << sec << ","
                        << mibps << ","
                        << minflt_delta << ","
                        << majflt_delta << ","
                        << npages << ","
                        << pagesz << ","
                        << map_len << ","
                        << file_size << ","
                        << avail_dram << ","
                        << std::stoull(seed)
                        << "\n";
            }
        }
    }

    if (repeat_count > 1) {
        double time_p50 = median(times), time_p10 = percentile(times, 10),
        time_p90 = percentile(times, 90); 
        double thr_p50 = median(thrpts), thr_p10 = percentile(thrpts, 10),
        thr_p90 = percentile(thrpts, 90);
        long mn_p50 = std::lround(median(minflts));
        long mj_p50 = std::lround(median(majflts));

        log << "---- Summary ----\n"
              << "  Pattern      : " << pattern << "\n"
              << "  Madvise      : " << madv_flag << "\n"
              << "  Repeat       : " << repeat << "\n"
              << "  Time (s)     : p10=" << time_p10 
              << ", p50=" << time_p50 
              << ", p90=" << time_p90 << "\n"
              << "  Throughput   : p10=" << thr_p10 
              << " MiB/s, p50=" << thr_p50 
              << " MiB/s, p90=" << thr_p90 << " MiB/s\n"
              << "  Minflt (p50) : " << mn_p50 << "\n"
              << "  Majflt (p50) : " << mj_p50 << "\n"
              << std::endl;

        log << "minflts: ";
        for (const auto& val : minflts) {
            log << val << " ";
        }
        log << std::endl;

        log << "majflts: ";
        for (const auto& val : majflts) {
            log << val << " ";
        }
        log << std::endl;
    }

    munmap(base, map_len);
    close(fd); 
    return 0;

}