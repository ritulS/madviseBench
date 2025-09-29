#include <iostream>
#include <vector>
#include <string>
#include <algorithm>
#include <fcntl.h>
#include <random>
#include <unistd.h>
#include <chrono>
#include <sys/stat.h>
#include <sys/mman.h>
#include <sys/resource.h>

// ---------- stats helpers ----------
static double percentile(std::vector<double> v, double per) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    double pos = (per/100) * (v.size() - 1);
    size_t floor = static_cast<size_t>(pos);
    double deci = pos - floor;
    return v[floor] + deci * (v[floor+1] - v[floor]);
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
    if (base == MAP_FAILED) {perror("mmap"); close(fd); return nullptr;}
    if (madv_flag == "seq") {
        if (madvise(base, map_len, MADV_SEQUENTIAL) != 0){
            perror("madvise(MADV_SEQUENTIAL)");
        }
    } else if (madv_flag == "rand") {
        if (madvise(base, map_len, MADV_RANDOM) != 0){
            perror("amdvise(MADV_RANDOM)");
        }
    } // else default

    return base;
}

int main(int argc, char** argv) {

    if (argc < 7) {
        std::cerr << "Usage: " << argv[0]
                               << " FILE SIZE PATTERN: seq/rand/stride<N>" 
                               << "MADVISE_FLAG [SEED] REPEAT\n";
        return 1;
    }

    std::string file = argv[1];
    std::string size = argv[2];
    std::string pattern = argv[3];
    std::string madv_flag = argv[4];
    std::string seed = argv[5];
    std::string repeat = argv[6];

    std::cout << "---- EXP DETAILS ----" << "\n";
    std::cout << "file = " << file << ", ";
    std::cout << "size = " << size << "\n";
    std::cout << "pattern = " << pattern << ", ";
    std::cout << "madv_flag = " << madv_flag << ", ";
    std::cout << "seed = " << seed << ", ";
    std::cout << "repeat =" << repeat << "\n\n";

    if (!(pattern.rfind("stride:", 0) == 0 || pattern == "seq" || pattern == "rand")){
        std::cerr << "ERROR: PATTERN must be 'seq', 'rand' or 'stride<N>'\n";
        return 2;
    }
    if (!(madv_flag == "seq" || madv_flag == "rand" || madv_flag == "none")){
        std::cerr << "ERROR: MADVISE_FLAG must be 'seq' or 'rand'\n";
        return 2;
    }

    unsigned long long req_size = 0;
    try{
        req_size = std::stoull(size);
    } catch (...) {
        std::cerr << "ERROR: SIZE must be an integer number of bytes\n";
        return 2;
    }

    int fd = open(file.c_str(), O_RDONLY | O_NOATIME);
    if (fd < 0){
        perror("open");
        return 1;
    }

    struct stat st{};
    if (fstat(fd, &st) != 0){
        perror("fstat");
        close(fd);
        return 1;
    }

    size_t file_size = static_cast<size_t>(st.st_size);

    if (file_size == 0) {
        std::cerr << "ERROR: file is empty\n";
        close(fd);
        return 2;
    }

    size_t map_len = req_size <= file_size ? req_size : file_size;
    // -----------------------------
    // Initial map & advise (first run)
    // -----------------------------
    void* base = map_and_advise(fd, map_len, madv_flag);
    if (base == MAP_FAILED) {perror("mmap"); close(fd); return 1;}

    // ------------------------------------------
    // PAGE SIZE & ACCESS ORDER (seq/rand)
    // ------------------------------------------
    const long pagesz = sysconf(_SC_PAGESIZE);
    const size_t npages = (map_len + pagesz - 1) / pagesz;

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

    for (int run = 0; run < repeat_count; run++) {

        // Clearing Page Cache for this file
        int pf = posix_fadvise(fd, 0, map_len, POSIX_FADV_DONTNEED);
        if (pf != 0) {
            perror("posix_fadvise");
        }
        // Drop PTEs by unmapping, then remap and re-apply advice
        if (run > 0){
            munmap(base, map_len);
            base = map_and_advise(fd, map_len, madv_flag);
            if (base == MAP_FAILED) {perror("mmap"); close(fd); return 1;}
            p = static_cast<volatile uint8_t*>(base);

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

        // std::cout << "---- RESULTS ----" << "\n";
        // std::cout << " size= " << map_len << "\n"
        //         << " pages= " << npages << "\n"
        //         << " acc_pattern= " << pattern << "\n"
        //         << " madvise= " << madv_flag << "\n"
        //         << " time_s= " << sec << "\n"
        //         << " throughput_MiBps= " << mibps << "\n"
        //         << " minflt= " << minflt_delta << "\n"
        //         << " majflt= " << majflt_delta << "\n";
        
        times.push_back(sec);
        thrpts.push_back(mibps);
        minflts.push_back(minflt_delta);
        majflts.push_back(majflt_delta);
    }

    if (repeat_count > 1) {
        double time_p50 = median(times), time_p10 = percentile(times, 10),
        time_p90 = percentile(times, 90); 
        double thr_p50 = median(thrpts), thr_p10 = percentile(thrpts, 10),
        thr_p90 = percentile(thrpts, 90);
        long mn_p50 = std::lround(median(minflts));
        long mj_p50 = std::lround(median(majflts));

        std::cout << "Summary" << "\n"
                  << " pattern=" << pattern << "\n"
                  << " madvise=" << madv_flag << "\n"
                  << " repeat=" << repeat << "\n"
                  << " time_s_p10=" << time_p10 << "\n"
                  << " time_s_p50=" << time_p50 << "\n"
                  << " time_s_p90=" << time_p90 << "\n"
                  << " thr_MiBps_p10=" << thr_p10 << "\n"
                  << " thr_MiBps_p50=" << thr_p50 << "\n"
                  << " thr_MiBps_p90=" << thr_p90 << "\n"
                  << " minflt_p50=" << mn_p50 << "\n"
                  << " majflt_p50=" << mj_p50 << "\n"
                  << "\n";

        std::cout << "minflts: ";
        for (const auto& val : minflts) {
            std::cout << val << " ";
        }
        std::cout << std::endl;

        std::cout << "majflts: ";
        for (const auto& val : majflts) {
            std::cout << val << " ";
        }
        std::cout << std::endl;
    }

    munmap(base, map_len);
    close(fd); 
    return 0;

}