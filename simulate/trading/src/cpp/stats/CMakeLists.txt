add_library(stats STATIC
    DiracDeltaDistribution.cpp
    Distribution.hpp
    DistributionFactory.cpp
    GammaDistribution.cpp
    LognormalDistribution.cpp
    RayleighDistribution.cpp
)
add_library(taosim::stats ALIAS stats)

target_include_directories(stats PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")

target_link_libraries(stats
    PUBLIC
        Boost::boost
        pugixml::static pugixml::pugixml
)