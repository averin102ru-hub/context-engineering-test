CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O2 -pthread

all: data_processor

data_processor: data_processor.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

clean:
	rm -f data_processor report.txt

.PHONY: all clean
