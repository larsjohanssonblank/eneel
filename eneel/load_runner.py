import eneel.utils as utils
from concurrent.futures import ProcessPoolExecutor as Executor
import os
import eneel.printer as printer
import time

import logging
logger = logging.getLogger('main_logger')


def run_project(project_name, connections_path=None):
    # Get configurations
    connections_config = utils.get_connections(connections_path)
    project_config = utils.get_project(project_name)

    source_name = project_config['source']
    target_name = project_config['target']

    source_conninfo = connections_config[source_name]
    target_conninfo = connections_config[target_name]

    project = project_config.copy()
    del project['schemas']

    # Create temp dir
    temp_path = project.get('temp_path', 'temp')
    temp_path = temp_path + '/' + project_name
    temp_path = utils.create_path(temp_path)

    # Lists of load settings
    load_orders = []
    project_names = []
    source_conninfos = []
    target_conninfos = []
    projects = []
    schemas = []
    tables = []
    temp_paths = []

    # Populate load settings
    for schema_config in project_config['schemas']:
        schema = schema_config.copy()
        del schema['tables']
        order_num = 1
        for table in schema_config['tables']:
            source_conninfo_item = source_conninfo
            target_conninfo_item = target_conninfo
            project_item = project
            schema_item = schema
            table_item = table

            load_orders.append(order_num)
            order_num += 1
            project_names.append(project_name)
            source_conninfos.append(source_conninfo_item)
            target_conninfos.append(target_conninfo_item)
            projects.append(project_item)
            schemas.append(schema_item)
            tables.append(table_item)
            temp_paths.append(temp_path)

    # Parallel load settings
    num_tables_to_load = len(tables)

    num_tables_to_loads = []
    for i in range(num_tables_to_load):
        num_tables_to_loads.append(num_tables_to_load)

    workers = project.get('parallel_loads', 1)

    printer.print_msg('Running ' + project_name
                      + ' with ' + str(num_tables_to_load) + ' loadjobs from '
                      + source_name + ' to ' + target_name
                      )
    printer.print_msg('')


    if num_tables_to_load < workers:
        workers = num_tables_to_load
    start_msg = "Start loading " + str(num_tables_to_load) + " tables with " + str(workers) + " parallel workers"
    #logger.info("Start loading " + str(num_tables_to_load) + " tables with " + str(workers) + " parallel workers")
    printer.print_output_line(start_msg)

    job_start_time = time.time()

    # Execute parallel load
    with Executor(max_workers=workers) as executor:
        for _ in executor.map(run_load, load_orders, num_tables_to_loads, project_names, source_conninfos, target_conninfos,
                              projects, temp_paths, schemas, tables):
            pass

    # Clean up temp dir
    utils.delete_path(temp_path)

    execution_time = time.time() - job_start_time

    status_time = " in {execution_time:0.2f}s".format(
        execution_time=execution_time)

    end_msg = "Finished loading " + str(num_tables_to_load) + " tables in " + status_time
    printer.print_output_line("")
    printer.print_output_line(end_msg)
    #logger.info("Finished loading " + str(num_tables_to_load) + " tables ")

    printer.print_msg("")
    printer.print_msg("Completed successfully", "green")


def run_load(load_order, num_tables_to_load, project_name, source_conninfo, target_conninfo, project, temp_path, schema, table):
    load_start_time = time.time()

    import eneel.logger as logger
    logger = logger.get_logger(project_name)

    # Remove duplicated handler if any
    for handler in logger.handlers[2:]:
        logger.removeHandler(handler)

    source = utils.connection_from_config(source_conninfo)
    target = utils.connection_from_config(target_conninfo)

    # Delimiter
    csv_delimiter = project.get('csv_delimiter')

    # Schemas
    source_schema = schema.get('source_schema')
    target_schema = schema.get('target_schema')

    # Tables
    source_table = table.get('table_name')
    full_source_table = source_schema + '.' + source_table
    target_table = schema.get('table_prefix', "") + table.get('table_name') + schema.get('table_suffix', "")
    full_target_table = target_schema + '.' + target_table

    # Temp table
    target_table_tmp = target_table + '_tmp'

    # Temp path for specific load
    temp_path_schema = os.path.join(temp_path, source_schema)
    temp_path_load = os.path.join(temp_path_schema, source_table)
    utils.create_path(temp_path_load)

    # Source column types to exclude
    source_columntypes_to_exclude = project.get('source_columntypes_to_exclude')
    if source_columntypes_to_exclude:
        source_columntypes_to_exclude = source_columntypes_to_exclude.lower().replace(" ", "").split(",")

    # Columns to load
    try:
        columns = source.table_columns(source_schema, source_table)
        if source_columntypes_to_exclude:
            columns_to_load = columns.copy()
            for col in columns:
                data_type = col[2].lower()
                if data_type in source_columntypes_to_exclude:
                    columns_to_load.remove(col)
            columns = columns_to_load
    except:
        logger.error("Could not determine columns to load")

    # Load type
    replication_method = table.get('replication_method')

    if not replication_method or replication_method == "FULL_TABLE":
        index = load_order
        total = num_tables_to_load
        status = "START"
        printer.print_load_line(index, total, status, full_source_table)
        #start_table_load_msg = "Start loading: " + full_source_table + " using FULL_TABLE replication"
        #printer.print_output_line(start_table_load_msg)
        #logger.info("Start loading: " + full_source_table + " using FULL_TABLE replication")
        # Export table
        file, delimiter = source.export_table(source_schema, source_table, columns, temp_path_load,
                                              csv_delimiter)

        # Create temp table
        target.create_table_from_columns(target_schema, target_table_tmp, columns)

        # Import into temp table
        import_status, import_row_count = target.import_table(target_schema, target_table_tmp, file, delimiter)

        # Switch tables
        target.switch_tables(target_schema, target_table, target_table_tmp)

        #msg = "DONE loading " + full_source_table

        #printer.print_fancy_output_line(msg, import_status, 1, 2)

    elif replication_method == "INCREMENTAL":
        index = load_order
        total = num_tables_to_load
        status = "START"
        printer.print_load_line(index, total, status, full_source_table)

        #logger.info("Start loading: " + full_source_table + " using INCREMENTAL replication")
        replication_key = table.get('replication_key')

        if target.check_table_exist(full_target_table) and replication_key:
            max_replication_key = target.get_max_column_value(full_target_table, replication_key)
            # Export new rows
            file, delimiter = source.export_table(source_schema, source_table, columns, temp_path_load, csv_delimiter,
                                                  replication_key, max_replication_key)
            # Create temp table
            target.create_table_from_columns(target_schema, target_table_tmp, columns)
            # Import into temp table
            import_status, import_row_count = target.import_table(target_schema, target_table_tmp, file, delimiter)
            # Insert into and drop
            target.insert_from_table_and_drop(target_schema, target_table, target_table_tmp)
            #logger.info(full_source_table + " updated")

        else:
            # Full export
            file, delimiter = source.export_table(source_schema, source_table, columns, temp_path_load, csv_delimiter)
            # Create temp table
            target.create_table_from_columns(target_schema, target_table_tmp, columns)
            # Import into temp table
            import_status, import_row_count = target.import_table(target_schema, target_table_tmp, file, delimiter)
            # Switch tables
            target.switch_tables(target_schema, target_table, target_table_tmp)
            logger.info(full_source_table + " imported")

        # Import table



    else:
        logger.error("replication_method not valid")

    # delete temp folder
    utils.delete_path(temp_path_load)

    end_table_load_msg = "Finished loading: " + full_source_table
    #printer.print_output_line(end_table_load_msg)

    #printer.print_fancy_output_line(msg, "OK", 1, 2)

    index = 1
    total = 2
    status = import_status
    rows = import_row_count
    execution_time = time.time() - load_start_time
    printer.print_load_line(index, total, status, full_source_table, rows, execution_time)

    #logger.info("Finished loading: " + full_source_table)
