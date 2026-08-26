/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Order.hpp"

#include <msgpack.hpp>

//-------------------------------------------------------------------------

namespace taosim::simulation::serialization
{

struct LimitOrder
{
    ::LimitOrder::Ptr limitOrder;
    std::optional<OrderID> clientOrderId;
};

}  // namespace taosim::simulation::serialization

//-------------------------------------------------------------------------

namespace msgpack
{

MSGPACK_API_VERSION_NAMESPACE(MSGPACK_DEFAULT_API_NS)
{

namespace adaptor
{

template<>
struct pack<taosim::simulation::serialization::LimitOrder>
{
    template<typename Stream>
    msgpack::packer<Stream>& operator()(
        msgpack::packer<Stream>& o, const taosim::simulation::serialization::LimitOrder& v) const
    {
        using namespace std::string_literals;

        o.pack_map(8);

        o.pack("y"s);
        o.pack("o"s);

        o.pack("i"s);
        o.pack(v.limitOrder->id());
    
        o.pack("c"s);
        o.pack(v.clientOrderId);

        o.pack("t"s);
        o.pack(v.limitOrder->timestamp());

        o.pack("q"s);
        o.pack(v.limitOrder->volume());

        o.pack("s"s);
        o.pack(std::to_underlying(v.limitOrder->direction()));

        o.pack("p"s);
        o.pack(v.limitOrder->price());

        o.pack("l"s);
        o.pack(v.limitOrder->leverage());

        return o;
    }
};

}  // namespace adaptor

}  // MSGPACK_API_VERSION_NAMESPACE

}  // namespace msgpack

//-------------------------------------------------------------------------