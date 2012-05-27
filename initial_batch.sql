drop table if exists map_results;
create table map_results(
    key text,
    value text,
    depth integer
);
drop index if exists map_results_idx;
create index map_results_idx on map_results(depth asc, key asc);

drop table if exists reduce_results;
create table reduce_results(
    key text unique primary key,
    value text
);
drop index if exists reduce_results_idx;
create index reduce_results_idx on reduce_results(key asc);

drop table if exists state;
create table state(
    current_state int
);
insert into state values (0);