create table map_results(
    key text,
    value text
);
create index if not exists map_results_idx on map_results(key asc);

create table reduce_results(
    key text unique primary key,
    value text
);
create index if not exists reduce_results_idx on reduce_results(key asc);

create table state(
    current_state int
);
insert into state values (0);