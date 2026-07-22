find_package(benchmark CONFIG REQUIRED)

add_executable(benchmarks SimulationBenchmarks.cpp)

target_include_directories(benchmarks
    PRIVATE
        $<TARGET_PROPERTY:taosim::simulation,INCLUDE_DIRECTORIES>
)

target_link_libraries(benchmarks
    PRIVATE
        taosim::simulation
        benchmark::benchmark benchmark::benchmark_main
        fmt::fmt
)
