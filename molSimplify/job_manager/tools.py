import os,glob,sys
import copy
import numpy as np
import subprocess
import pandas as pd
import shutil
import time
from molSimplify.job_manager.classes import resub_history, textfile
import molSimplify.job_manager.manager_io as manager_io
from ast import literal_eval


def ensure_dir(dirpath):
    if not os.path.isdir(dirpath):
        os.makedirs(dirpath)


def check_valid_outfile(path):
    # The nohup.out file gets caught in the find statement
    # use this function so that we only get TeraChem.outs
    endpath = os.path.split(path)[-1]
    if 'nohup.out' in endpath or endpath.startswith('.') or any("_v%d." % x in endpath for x in range(10)):
        return False
    elif 'slurm-' in endpath:
        return False
    else:
        return True


def priority_sort(lst_of_lsts):
    # Takes a list of lists
    # This function ensures that each entry occurs in only one list
    # If an entry occurs in multiple lists, it is retained in the list which appears first in the list of lists

    lst_of_lsts.reverse()  # reverse the list so low priority occurs first

    new_lst_of_lsts = []
    for counter in range(len(lst_of_lsts)):
        local_lst = lst_of_lsts[counter]
        higher_priority_lists = lst_of_lsts[counter + 1:]

        for high_priority in higher_priority_lists:
            local_lst = list(set(local_lst) - set(high_priority))

        new_lst_of_lsts.append(local_lst)

    new_lst_of_lsts.reverse()  # undo the original reversal

    return new_lst_of_lsts

def call_bash(string, error=False, version=1):
    if version == 1:
        p = subprocess.Popen(string.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    elif version == 2:
        p = subprocess.Popen(string, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    out, err = p.communicate()

    if sys.version_info > (3,0):
        out = out.decode('utf-8')
        err = err.decode('utf-8')

    out = out.split('\n')
    if out[-1] == '':
        out = out[:-1]

    if error:
        return out, err
    else:
        return out

def get_machine():
    # Gets the identity of the machine job_manager is being run on!
    hostname = call_bash('hostname')[0]
    if 'bridges.psc' in hostname:
        machine = 'bridges'
    elif 'gibraltar' in hostname:
        machine = 'gibraltar'
    elif 'comet' in hostname:
        machine = 'comet'
    elif 'home' in hostname:
        machine = 'gibraltar'
    else:
        raise ValueError('Machine Unknown to Job Manager')
    return machine

# Set user and machine as global variables before analysis
machine = get_machine()
username = call_bash('whoami')[0]

def convert_to_absolute_path(path):
    if path[0] != '/':
        path = os.path.join(os.getcwd(), path)

    return path

def create_summary(directory='in place'):
    # Returns a pandas dataframe which summarizes all outfiles in the directory, defaults to cwd

    outfiles = find('*.out', directory)
    outfiles = list(filter(check_valid_outfile, outfiles))
    results = list(map(manager_io.read_outfile, outfiles))
    summary = pd.DataFrame(results)

    return summary


def list_active_jobs(ids=False, home_directory=False, parse_bundles=False):
    #  @return A list of active jobs for the current user. By job name

    if (ids and parse_bundles) or (parse_bundles and not home_directory):
        raise Exception('Incompatible options passed to list_active_jobs()')
    if home_directory == 'in place':
        home_directory = os.getcwd()
    
    job_report = textfile()
    try:
        if machine == 'gibraltar':
            job_report.lines = call_bash("qstat -r")
        elif machine in ['comet','bridges']:
            job_report.lines = call_bash('squeue -o "%.18i %.9P %.50j %.8u %.2t %.10M %.6D %R" -u '+username, 
                                          version=2)
        else: 
            raise ValueError
    except:
        job_report.lines = []
    if machine == 'gibraltar':
        names = job_report.wordgrab('jobname:', 2)[0]
        names = [i for i in names if i]  # filters out NoneTypes
    elif machine in ['comet','bridges']:
        names = job_report.wordgrab(username, 2)[0]
        names = [i for i in names if i] # filters out NoneTypes
    else:
        raise ValueError
    if ids:
        job_ids = []
        if machine == 'gibraltar':
            line_indices_of_jobnames = job_report.wordgrab('jobname:', 2, matching_index=True)[0]
        elif machine in ['comet','bridges']:
            line_indices_of_jobnames = job_report.wordgrab(username, 2, matching_index=True)[0]
        line_indices_of_jobnames = [i for i in line_indices_of_jobnames if i]  # filters out NoneTypes
        for line_index in line_indices_of_jobnames:
            if machine == 'gibraltar':
                job_ids.append(int(job_report.lines[line_index - 1].split()[0]))
            elif machine in ['comet','bridges']:
                job_ids.append(int(job_report.lines[line_index].split()[0]))
        if len(names) != len(job_ids):
            print(len(names))
            print(len(job_ids))
            raise Exception('An error has occurred in listing active jobs!')
        return names, job_ids

    if parse_bundles and os.path.isfile(os.path.join(home_directory, 'bundle', 'bundle_id')):

        fil = open(os.path.join(home_directory, 'bundle', 'bundle_id'), 'r')
        identifier = fil.readlines()[0]
        fil.close()

        bundles = [i for i in names if i.startswith('bundle_')]
        bundles = [i.rsplit('_', 1)[0] for i in names if i.endswith(identifier)]
        names = [i for i in names if i not in bundles]

        for bundle in bundles:
            info_path = glob.glob(os.path.join(home_directory, 'bundle', bundle, '*_info'))[0]
            fil = open(info_path, 'r')
            lines = fil.readlines()
            lines = [i[:-1] if i.endswith('\n') else i for i in lines]
            fil.close()
            names.extend(lines)

    return names


def get_number_active():
    # gets the number of jobs in the queue for this user, only counts jobs from this job directory
    active_jobs = list_active_jobs()

    def check_active(path, active_jobs=active_jobs):
        # Given a path, checks if it's in the queue currently:
        name = os.path.split(path)[-1]
        name = name.rsplit('.', 1)[0]
        if name in active_jobs:
            return True
        else:
            return False

    outfiles = find('*.out')
    outfiles = [i for i in outfiles if check_valid_outfile(i)]

    active_non_bundles = [i for i in outfiles if check_active(i)]

    if os.path.isdir('bundle'):
        fil = open(os.path.join('bundle', 'bundle_id'))
        identifier = fil.readlines()[0]
        if identifier.endswith('\n'):
            identifier = identifier[:-1]
        fil.close()

        active_bundles = [i for i in active_jobs if i.startswith('bundle_')]
        active_bundles = [i for i in active_jobs if i.endswith(identifier)]
        active_jobs = active_bundles + active_non_bundles

        return len(active_jobs)
    else:
        return len(active_non_bundles)


def get_total_queue_usage():
    # gets the number of jobs in the queue for this user, regardless of where they originate
    if machine == 'gibraltar':
        jobs = call_bash("qstat -u '" + username + "'", version=2)
    elif machine in ['comet','bridges']:
        jobs = call_bash('squeue -o "%.18i %.9P %.50j %.8u %.2t %.10M %.6D %R" -u ' + username,
                         version=2)
    else:
        raise ValueError('Job manager does not know this machine!')
    jobs = [i for i in jobs if username in i.split()]

    return len(jobs)


def check_completeness(directory='in place', max_resub=5, configure_dict=False):
    ## Takes a directory, returns lists of finished, failed, and in-progress jobs
    outfiles = find('*.out', directory)
    outfiles = list(filter(check_valid_outfile, outfiles))

    results_tmp = [manager_io.read_outfile(outfile, short_ouput=True) for outfile in outfiles]
    results_tmp = list(zip(outfiles, results_tmp))
    results_dict = dict()
    for outfile, tmp in results_tmp:
        results_dict[outfile] = tmp
        # print(outfile, tmp['oscillating_scf_error'])

    active_jobs = list_active_jobs(home_directory=directory, parse_bundles=True)

    def check_finished(path, results_dict=results_dict):
        # Return True if the outfile corresponds to a complete job, False otherwise
        if results_dict[path]['finished']:
            return True
        else:
            return False

    def check_active(path, active_jobs=active_jobs):
        # Given a path, checks if it's in the queue currently:
        name = os.path.split(path)[-1]
        name = name.rsplit('.', 1)[0]
        if name in active_jobs:
            return True
        else:
            return False

    def check_needs_resub(path):
        if os.path.isfile(path.rsplit('.', 1)[0] + '.pickle'):
            history = resub_history()
            history.read(path)
            return history.needs_resub
        else:
            return False

    def check_waiting(path):
        if os.path.isfile(path.rsplit('.', 1)[0] + '.pickle'):
            history = resub_history()
            history.read(path)
            if history.waiting:
                return True
        return False

    def grab_waiting(path):
        if os.path.isfile(path.rsplit('.', 1)[0] + '.pickle'):
            history = resub_history()
            history.read(path)
            return history.waiting
        raise Exception('Attempting to grab a "waiting" criteria that does not exist')

    def check_chronic_failure(path):
        if os.path.isfile(path.rsplit('.', 1)[0] + '.pickle'):
            history = resub_history()
            history.read(path)
            if history.resub_number >= max_resub:
                return True
        else:
            return False

    def check_spin_contaminated(path, results_dict=results_dict):
        results = results_dict[path]
        if configure_dict and "ss_cutoff" in configure_dict:
            ss_cutoff = configure_dict['ss_cutoff']
        else:
            ss_cutoff = 1.0
        if results['finished']:
            if type(results['s_squared_ideal']) == float:
                if abs(results['s_squared'] - results['s_squared_ideal']) > ss_cutoff:
                    return True
        return False

    def check_oscillating_scf_error(path, results_dict=results_dict):
        results = results_dict[path]
        if results['oscillating_scf_error']:
            return True
        else:
            return False

    def check_scf_error(path, results_dict=results_dict):
        results = results_dict[path]
        if results['scf_error']:
            return True
        else:
            return False

    def check_thermo_grad_error(path, results_dict=results_dict):
        results = results_dict[path]
        if results['thermo_grad_error']:
            return True
        else:
            return False

    active_jobs = list(filter(check_active, outfiles))
    finished = list(filter(check_finished, outfiles))
    needs_resub = list(filter(check_needs_resub, outfiles))
    waiting = list(filter(check_waiting, outfiles))
    spin_contaminated = list(filter(check_spin_contaminated, outfiles))
    all_scf_errors = list(filter(check_scf_error, outfiles))
    oscillating_scf_errors = list(filter(check_oscillating_scf_error, outfiles))
    thermo_grad_errors = list(filter(check_thermo_grad_error, outfiles))
    chronic_errors = list(filter(check_chronic_failure, outfiles))
    errors = list(set(outfiles) - set(active_jobs) - set(finished))
    scf_errors = list(filter(check_scf_error, errors))

    # Look for additional active jobs that haven't yet generated outfiles
    jobscript_list = find('*_jobscript', directory)
    jobscript_list = [i.rsplit('_', 1)[0] + '.out' for i in jobscript_list]
    extra_active_jobs = list(filter(check_active, jobscript_list))
    active_jobs.extend(extra_active_jobs)

    # Sort out conflicts in order of reverse priority
    # A job only gets labelled as finished if it's in no other category
    # A job always gets labelled as active if it fits that criteria, even if it's in every other category too

    priority_list = [active_jobs, chronic_errors, waiting, thermo_grad_errors,
                     oscillating_scf_errors, scf_errors, errors, spin_contaminated, needs_resub, finished]
    priority_list_names = ['Active', 'Chronic_errors', 'Waiting', 'Thermo_grad_error',
                           'oscillating_scf_errors', 'SCF_Error', 'Error', 'Spin_contaminated', 'Needs_resub',
                           'Finished']
    priority_list = priority_sort(priority_list)

    results = dict()
    for key, lst in zip(priority_list_names, priority_list):
        results[key] = lst

    # There are two special categories which operate a bit differently: waiting and "SCF_Errors_Including_Active"
    waiting = [{i: grab_waiting(i)} for i in waiting]
    results['Waiting'] = waiting
    results['SCF_Errors_Including_Active'] = all_scf_errors

    return results


def find(key, directory='in place'):
    ## Looks for all files with a matching key in their name within directory
    #  @return A list of paths
    if directory == 'in place':
        directory = os.getcwd()

    bash = 'find ' + directory + ' -name ' + key

    return call_bash(bash)


def qsub(jobscript_list):
    ## Takes a list of paths to jobscripts (like output by find) and submits them with qsub
    # Handles cases where a single jobscript is submitted instead of a list
    if type(jobscript_list) != list:
        jobscript_list = [jobscript_list]
    jobscript_list = [convert_to_absolute_path(i) for i in jobscript_list]

    stdouts = []
    for i in jobscript_list:
        home = os.getcwd()
        os.chdir(os.path.split(i)[0])
        jobscript = os.path.split(i)[1]
        if machine in ['gibraltar']:
            stdout, stderr = call_bash('qsub ' + jobscript, error=True)
            stdouts.append(stdout)
        elif machine in ['bridges','comet']:
            stdout, stderr = call_bash('sbatch ' + jobscript, error=True)
            stdouts.append(stdout)
        if len(stderr) > 0:
            raise Exception(stderr)
        os.chdir(home)
        time.sleep(1)

    return stdouts


def check_original(job):
    # Returns true if this job is an original job
    # Returns false if this job is already a derivative job

    name = os.path.split(job)[-1]
    name = name.rsplit('.', 1)[0]
    name = name.split('_')

    dependent_jobs = ['solvent', 'vertEA', 'vertIP', 'thermo', 'kp', 'rm', 'ultratight', 'HFXresampling',
                      'functional']
    if any(j in name for j in dependent_jobs):
        return False
    else:
        return True


def check_short_single_point(job):
    name = os.path.split(job)[-1]
    name = name.rsplit('.', 1)[0]
    name = name.split('_')

    short_jobs = ['solvent', 'kp', 'rm', 'functional', 'vertEA', 'vertIP']
    if any(j in name for j in short_jobs):
        return True
    else:
        return False


def extract_optimized_geo(PATH, custom_name=False):
    # Given the path to an optim.xyz file, this will extract optimized.xyz, which contains only the last frame
    # The file is written to the same directory as contained optim.xyz

    optim = open(PATH, 'r')
    lines = optim.readlines()
    optim.close()
    lines.reverse()
    if len(lines) == 0:
        lines = []
        print(('optim.xyz is empty for: ' + PATH))
    else:
        for i in range(len(lines)):
            if 'frame' in lines[i].split():
                break
        lines = lines[:i + 2]
        lines.reverse()
    homedir = os.path.split(PATH)[0]

    if custom_name:
        basedir = os.path.split(homedir)[0]
        xyz = glob.glob(os.path.join(basedir, '*.xyz'))[0]
        xyz = os.path.split(xyz)[-1]
        name = xyz[:-4] + '_optimized.xyz'
    else:
        name = 'optimized.xyz'

    optimized = open(os.path.join(homedir, name), 'w')
    for i in lines:
        optimized.write(i)
    optimized.close()

    return lines


def pull_optimized_geos(PATHs=[]):
    # Given a PATH or list of PATHs to optim.xyz files, these will grab the optimized geometries and move them into a local folder called optimized_geos
    if type(PATHs) != list:
        PATHs = [PATHs]
    if len(PATHs) == 0:
        PATHs = find('optim.xyz')  # Default behavior will pull all optims in the current directory

    if os.path.isdir('opimized_geos'):
        raise Exception('optimized_geos already exists!')

    os.mkdir('optimized_geos')
    home = os.getcwd()
    for Path in PATHs:
        extract_optimized_geo(Path)
        scr_path = os.path.split(Path)[0]
        homedir = os.path.split(scr_path)[0]
        initial_xyz = glob.glob(os.path.join(homedir, '*.xyz'))
        if len(initial_xyz) != 1:
            print('Name could not be identified for: ' + Path)
            print('Naming with a random 6 digit number')
            name = str(np.random.randint(999999)) + '_optimized.xyz'
        else:
            initial_xyz = initial_xyz[0]
            name = os.path.split(initial_xyz)[-1]
            name = name.rsplit('.', 1)[0]
            name += '_optimized.xyz'

        shutil.move(os.path.join(os.path.split(Path)[0], 'optimized.xyz'), os.path.join(home, 'optimized_geos', name))


def bundle_jobscripts(home_directory, jobscript_paths, max_bundle_size=10):
    number_of_bundles = int(float(len(jobscript_paths)) / float(max_bundle_size))

    bundles, i, many_bundles = [], 0, False
    for i in range(number_of_bundles):
        bundles.append(jobscript_paths[max_bundle_size * i:max_bundle_size * (i + 1)])
        many_bundles = True
    if many_bundles:
        total_length = [len(ii) for ii in bundles]
        total_length = np.sum(np.array(total_length))
        if total_length != len(
                jobscript_paths):  # Triggers when the number of jobscripts is not divisable by the bundle size
            bundles.append(jobscript_paths[max_bundle_size * (i + 1):])
    else:  # Triggers when there are fewere jobscripts than the bundle size
        bundles = [jobscript_paths]
    print('Bundling ' + str(len(jobscript_paths)) + ' short jobs into ' + str(len(bundles)) + ' jobscript(s)')

    output_jobscripts = []
    for bundle in bundles:
        output_jobscripts.append(sub_bundle_jobscripts(home_directory, bundle))

    return output_jobscripts


def sub_bundle_jobscripts(home_directory, jobscript_paths):
    # Takes a list of jobscript paths, and bundles them into a single jobscript
    # Records information about which jobs were bundled together in the run's home directory
    if not os.path.isdir(os.path.join(home_directory, 'bundle')):
        os.mkdir(os.path.join(home_directory, 'bundle'))
        fil = open(os.path.join(home_directory, 'bundle', 'bundle_id'), 'w')
        fil.write(str(np.random.randint(100000000000)))
        fil.close()
    fil = open(os.path.join(home_directory, 'bundle', 'bundle_id'), 'r')
    identifier = fil.readlines()[0]
    fil.close()

    jobscript_paths = [convert_to_absolute_path(i) for i in jobscript_paths]

    existing_bundles = glob.glob(os.path.join(home_directory, 'bundle', '*'))
    existing_bundles = [i for i in existing_bundles if os.path.isdir(i)]
    if len(existing_bundles) > 0:
        existing_bundle_numbers = [int(os.path.split(i)[-1].split('_')[-1]) for i in existing_bundles]
    else:
        existing_bundle_numbers = [0]

    # Create a directory for this jobscript bundle
    os.mkdir(os.path.join(home_directory, 'bundle', 'bundle_' + str(max(existing_bundle_numbers) + 1)))

    # Record info about how the jobs are being bundled
    fil = open(os.path.join(home_directory, 'bundle', 'bundle_' + str(max(existing_bundle_numbers) + 1),
                            'bundle_' + str(max(existing_bundle_numbers) + 1) + '_info'), 'w')
    for i in jobscript_paths[:-1]:
        fil.write(os.path.split(i)[-1].rsplit('_', 1)[0] + '\n')
    fil.write(os.path.split(jobscript_paths[-1])[-1].rsplit('_', 1)[0])
    fil.close()

    # Write a jobscript for the job bundle
    home = os.getcwd()
    os.chdir(os.path.join(home_directory, 'bundle', 'bundle_' + str(max(existing_bundle_numbers) + 1)))
    manager_io.write_terachem_jobscript(str('bundle_' + str(max(existing_bundle_numbers) + 1)) + '_' + identifier,
                                        terachem_line=False, time_limit='12:00:00',machine=machine)
    shutil.move('bundle_' + str(max(existing_bundle_numbers) + 1) + '_' + identifier + '_jobscript',
                'bundle_' + str(max(existing_bundle_numbers) + 1))
    fil = open('bundle_' + str(max(existing_bundle_numbers) + 1), 'a')
    for i in jobscript_paths:
        infile = i.rsplit('_', 1)[0] + '.in'
        outfile = i.rsplit('_', 1)[0] + '.out'
        directory = os.path.split(i)[0]
        text = 'cd ' + directory + '\n' + 'terachem ' + infile + ' > ' + outfile
        fil.write(text + '\n')
    fil.close()
    os.chdir(home)

    return os.path.join(home_directory, 'bundle', 'bundle_' + str(max(existing_bundle_numbers) + 1),
                        'bundle_' + str(max(existing_bundle_numbers) + 1))


def prep_vertical_ip(path):
    # Given a path to the outfile of a finished run, this preps the files for a corresponding vertical IP run
    # Returns a list of the PATH(s) to the jobscript(s) to start the vertical IP calculations(s)
    home = os.getcwd()
    path = convert_to_absolute_path(path)

    results = manager_io.read_outfile(path)
    if not results['finished']:
        raise Exception('This calculation does not appear to be complete! Aborting...')

    infile_dict = manager_io.read_infile(path)

    if infile_dict['spinmult'] == 1:
        new_spin = [2]
    else:
        new_spin = [infile_dict['spinmult'] - 1, infile_dict['spinmult'] + 1]

    base = os.path.split(path)[0]
    
    if infile_dict['run_type'] == 'minimize':
        optimxyz = os.path.join(base, 'scr', 'optim.xyz')
    else:
        optimxyz = os.path.join(base, 'scr', 'xyz.xyz')
    extract_optimized_geo(optimxyz)

    ipname = results['name'] + '_vertIP'
    vertip_base_path = os.path.join(base, ipname)
    if os.path.isdir(vertip_base_path):
        return ['Directory for vertIP single point already exists']
    os.mkdir(vertip_base_path)
    os.chdir(vertip_base_path)

    jobscripts = []
    for calc in new_spin:
        if calc < 7:
            name = results['name'] + '_vertIP_' + str(calc)
            PATH = os.path.join(vertip_base_path, str(calc))
            if os.path.isdir(PATH):
                jobscripts.append('File for vert IP spin ' + str(calc) + 'already exists')
            else:
                os.mkdir(PATH)
                os.chdir(PATH)

                shutil.copyfile(os.path.join(base, 'scr', 'optimized.xyz'), os.path.join(PATH, name + '.xyz'))

                local_infile_dict = copy.copy(infile_dict)
                local_infile_dict['charge'], local_infile_dict['guess'] = infile_dict['charge'] + 1, False
                local_infile_dict['run_type'], local_infile_dict['spinmult'] = 'energy', calc
                local_infile_dict['name'] = name
                local_infile_dict['levelshifta'], local_infile_dict['levelshiftb'] = 0.25, 0.25
                local_infile_dict['machine'] = machine
                manager_io.write_input(local_infile_dict)
                manager_io.write_jobscript(name,machine=machine)

                jobscripts.append(os.path.join(PATH, name + '_jobscript'))

    os.chdir(home)

    return jobscripts


def prep_vertical_ea(path):
    # Given a path to the outfile of a finished run, this preps the files for a corresponding vertical EA run
    # Returns a list of the PATH(s) to the jobscript(s) to start the vertical IP calculations(s)
    home = os.getcwd()
    path = convert_to_absolute_path(path)

    results = manager_io.read_outfile(path)
    if not results['finished']:
        raise Exception('This calculation does not appear to be complete! Aborting...')

    infile_dict = manager_io.read_infile(path)

    if infile_dict['spinmult'] == 1:
        new_spin = [2]
    else:
        new_spin = [infile_dict['spinmult'] - 1, infile_dict['spinmult'] + 1]

    base = os.path.split(path)[0]

    if infile_dict['run_type'] == 'minimize':
        optimxyz = os.path.join(base, 'scr', 'optim.xyz')
    else:
        optimxyz = os.path.join(base, 'scr', 'xyz.xyz')
    extract_optimized_geo(optimxyz)

    eaname = results['name'] + '_vertEA'
    vertea_base_path = os.path.join(base, eaname)
    if os.path.isdir(vertea_base_path):
        return ['Directory for vertEA single point already exists']
    os.mkdir(vertea_base_path)
    os.chdir(vertea_base_path)

    jobscripts = []
    for calc in new_spin:
        if calc < 7:
            name = results['name'] + '_vertEA_' + str(calc)
            PATH = os.path.join(vertea_base_path, str(calc))
            if os.path.isdir(PATH):
                jobscripts.append('File for vert EA spin ' + str(calc) + 'already exists')
            else:
                os.mkdir(PATH)
                os.chdir(PATH)
                shutil.copyfile(os.path.join(base, 'scr', 'optimized.xyz'), os.path.join(PATH, name + '.xyz'))

                local_infile_dict = copy.copy(infile_dict)
                local_infile_dict['charge'], local_infile_dict['guess'] = infile_dict['charge'] - 1, False
                local_infile_dict['run_type'], local_infile_dict['spinmult'] = 'energy', calc
                local_infile_dict['name'] = name
                local_infile_dict['levelshifta'], local_infile_dict['levelshiftb'] = 0.25, 0.25
                local_infile_dict['machine'] = machine

                manager_io.write_input(local_infile_dict)
                manager_io.write_jobscript(name, machine=machine)
                jobscripts.append(os.path.join(PATH, name + '_jobscript'))
    os.chdir(home)
    return jobscripts


def prep_solvent_sp(path, solvents=[78.9]):
    # Given a path to the outfile of a finished run, this preps the files for a single point solvent run
    # Uses the wavefunction from the gas phase calculation as an initial guess
    # Returns a list of the PATH(s) to the jobscript(s) to start the solvent sp calculations(s)
    home = os.getcwd()
    path = convert_to_absolute_path(path)

    results = manager_io.read_outfile(path)
    if not results['finished']:
        raise Exception('This calculation does not appear to be complete! Aborting...')

    infile_dict = manager_io.read_infile(path)

    base = os.path.split(path)[0]

    if infile_dict['run_type'] == 'minimize':
        optimxyz = os.path.join(base, 'scr', 'optim.xyz')
    else:
        optimxyz = os.path.join(base, 'scr', 'xyz.xyz')
    extract_optimized_geo(optimxyz)

    # Now, start generating the new directory
    solname = results['name'] + '_solvent'
    solvent_base_path = os.path.join(base, solname)
    # print(solvent_base_path)
    if os.path.isdir(solvent_base_path):
        # print('Directory for solvent single point already exists')
        pass
    else:
        os.mkdir(solvent_base_path)
    os.chdir(solvent_base_path)

    jobscripts = []
    for sol_val in solvents:
        PATH = os.path.join(solvent_base_path, str(sol_val).replace('.', '_'))
        if os.path.isdir(PATH):
            continue
        ensure_dir(PATH)
        name = results['name'] + "_solvent_" + str(sol_val)
        shutil.copyfile(os.path.join(base, 'scr', 'optimized.xyz'), os.path.join(PATH, name + '.xyz'))
        guess = False
        os.chdir(PATH)
        if infile_dict['spinmult'] == 1:
            if os.path.isfile(os.path.join(base, 'scr', 'c0')):
                shutil.copyfile(os.path.join(base, 'scr', 'c0'), os.path.join(PATH, 'c0'))
                manager_io.write_jobscript(name, custom_line='# -fin c0', machine=machine)
                guess = True
        else:
            if os.path.isfile(os.path.join(base, 'scr', 'ca0')) and os.path.isfile(os.path.join(base, 'scr', 'cb0')):
                shutil.copyfile(os.path.join(base, 'scr', 'ca0'), os.path.join(PATH, 'ca0'))
                shutil.copyfile(os.path.join(base, 'scr', 'cb0'), os.path.join(PATH, 'cb0'))
                manager_io.write_jobscript(name, custom_line=['# -fin ca0\n', '# -fin cb0\n'], machine=machine)
                guess = True
        local_infile_dict = copy.copy(infile_dict)
        local_infile_dict['solvent'], local_infile_dict['guess'] = sol_val, guess
        local_infile_dict['run_type'] = 'energy'
        local_infile_dict['name'] = name
        local_infile_dict['levelshifta'], local_infile_dict['levelshiftb'] = 0.25, 0.25
        local_infile_dict['machine'] = machine

        manager_io.write_input(local_infile_dict)

        os.chdir(home)
        jobscripts.append(os.path.join(PATH, name + '_jobscript'))
    os.chdir(home)
    return jobscripts


def prep_functionals_sp(path, functionalsSP):
    home = os.getcwd()
    path = convert_to_absolute_path(path)
    results = manager_io.read_outfile(path)
    if not results['finished']:
        raise Exception('This calculation does not appear to be complete! Aborting...')

    infile_dict = manager_io.read_infile(path)
    base = os.path.split(path)[0]

    if infile_dict['run_type'] == 'minimize':
        optimxyz = os.path.join(base, 'scr', 'optim.xyz')
    else:
        optimxyz = os.path.join(base, 'scr', 'xyz.xyz')
    extract_optimized_geo(optimxyz)

    # Now, start generating the new directory
    funcname = results['name'] + '_functionalsSP'
    functional_base_path = os.path.join(base, funcname)
    if os.path.isdir(functional_base_path):
        # print('Directory for functional single point already exists')
        pass
    else:
        os.mkdir(functional_base_path)
    os.chdir(functional_base_path)

    jobscripts = []
    for func in functionalsSP:
        PATH = os.path.join(functional_base_path, str(func))
        if os.path.isdir(PATH):
            continue
        ensure_dir(PATH)
        name = results['name'] + "_functional_" + str(func)
        shutil.copyfile(os.path.join(base, 'scr', 'optimized.xyz'), os.path.join(PATH, name + '.xyz'))
        guess = False
        os.chdir(PATH)
        if infile_dict['spinmult'] == 1:
            if os.path.isfile(os.path.join(base, 'scr', 'c0')):
                shutil.copyfile(os.path.join(base, 'scr', 'c0'), os.path.join(PATH, 'c0'))
                manager_io.write_jobscript(name, custom_line='# -fin c0', machine=machine)
                guess = True
        else:
            if os.path.isfile(os.path.join(base, 'scr', 'ca0')) and os.path.isfile(os.path.join(base, 'scr', 'cb0')):
                shutil.copyfile(os.path.join(base, 'scr', 'ca0'), os.path.join(PATH, 'ca0'))
                shutil.copyfile(os.path.join(base, 'scr', 'cb0'), os.path.join(PATH, 'cb0'))
                manager_io.write_jobscript(name, custom_line=['# -fin ca0\n', '# -fin cb0\n'], machine=machine)
                guess = True
        local_infile_dict = copy.copy(infile_dict)
        local_infile_dict['guess'] = guess
        local_infile_dict['run_type'] = 'energy'
        local_infile_dict['name'] = name
        local_infile_dict['levelshifta'], local_infile_dict['levelshiftb'] = 0.25, 0.25
        local_infile_dict['method'] = func
        local_infile_dict['machine'] = machine

        manager_io.write_input(local_infile_dict)

        fil = open('configure', 'w')
        fil.write('method:' + func)
        fil.close()
        os.chdir(home)
        jobscripts.append(os.path.join(PATH, name + '_jobscript'))
    os.chdir(home)
    return jobscripts


def prep_thermo(path):
    # Given a path to the outfile of a finished run, this preps the files for a thermo calculation
    # Uses the wavefunction from the previous calculation as an initial guess
    # Returns a list of the PATH(s) to the jobscript(s) to start the solvent sp calculations(s)
    home = os.getcwd()
    path = convert_to_absolute_path(path)

    results = manager_io.read_outfile(path)
    infile_dict = manager_io.read_infile(path)

    base = os.path.split(path)[0]

    if infile_dict['run_type'] == 'minimize':
        optimxyz = os.path.join(base, 'scr', 'optim.xyz')
    else:
        optimxyz = os.path.join(base, 'scr', 'xyz.xyz')
    extract_optimized_geo(optimxyz)

    # Now, start generating the new directory
    name = results['name'] + '_thermo'
    PATH = os.path.join(base, name)
    if os.path.isdir(PATH):
        return ['Thermo Calculation Directory already exists']

    os.mkdir(PATH)
    os.chdir(PATH)

    shutil.copyfile(os.path.join(base, 'scr', 'optimized.xyz'), os.path.join(PATH, name + '.xyz'))
    local_infile_dict = copy.copy(infile_dict)
    local_infile_dict['guess'] = True
    local_infile_dict['run_type'] = 'frequencies'
    local_infile_dict['name'] = name
    local_infile_dict['machine'] = machine

    manager_io.write_input(local_infile_dict)
    if infile_dict['spinmult'] == 1:
        shutil.copyfile(os.path.join(base, 'scr', 'c0'), os.path.join(PATH, 'c0'))
        manager_io.write_jobscript(name, custom_line='# -fin c0', machine=machine)
    if infile_dict['spinmult'] != 1:
        shutil.copyfile(os.path.join(base, 'scr', 'ca0'), os.path.join(PATH, 'ca0'))
        shutil.copyfile(os.path.join(base, 'scr', 'cb0'), os.path.join(PATH, 'cb0'))
        manager_io.write_jobscript(name, custom_line=['# -fin ca0\n', '# -fin cb0\n'], machine=machine)

    os.chdir(home)
    return [os.path.join(PATH, name + '_jobscript')]


def prep_ultratight(path):
    # Given a path to the outfile of a finished run, this preps a run with tighter convergence criteria
    # Uses the wavefunction and geometry from the previous calculation as an initial guess
    # Returns a list of the PATH(s) to the jobscript(s) to start the solvent sp calculations(s)
    home = os.getcwd()
    path = convert_to_absolute_path(path)

    results = manager_io.read_outfile(path)
    if not results['finished']:
        raise Exception('This calculation does not appear to be complete! Aborting...')

    infile_dict = manager_io.read_infile(path)

    base = os.path.split(path)[0]

    if infile_dict['run_type'] == 'minimize':
        optimxyz = os.path.join(base, 'scr', 'optim.xyz')
    else:
        optimxyz = os.path.join(base, 'scr', 'xyz.xyz')
    extract_optimized_geo(optimxyz)

    # Now, start generating the new directory
    name = results['name'] + '_ultratight'
    PATH = os.path.join(base, name)

    if not os.path.isdir(PATH):  # First time that ultratight has been run, create necessary files
        os.mkdir(PATH)
        os.chdir(PATH)

        if os.path.exists(name + '.in') or os.path.exists(name + '.out') or os.path.exists(name + '_jobscript'):
            raise Exception('This tightened convergence run appears to already exist. Aborting...')

        shutil.copyfile(os.path.join(base, 'scr', 'optimized.xyz'), os.path.join(PATH, name + '.xyz'))
        if infile_dict['spinmult'] == 1:
            shutil.copyfile(os.path.join(base, 'scr', 'c0'), os.path.join(PATH, 'c0'))
            manager_io.write_jobscript(name, custom_line='# -fin c0', machine=machine)
        elif infile_dict['spinmult'] != 1:
            shutil.copyfile(os.path.join(base, 'scr', 'ca0'), os.path.join(PATH, 'ca0'))
            shutil.copyfile(os.path.join(base, 'scr', 'cb0'), os.path.join(PATH, 'cb0'))
            manager_io.write_jobscript(name, custom_line=['# -fin ca0\n', '# -fin cb0\n'], machine=machine)

        criteria = ['2.25e-04', '1.5e-04', '0.9e-03', '0.6e-03', '0.5e-06', '1.5e-05']

        local_infile_dict = copy.copy(infile_dict)
        local_infile_dict['guess'] = True
        local_infile_dict['convergence_thresholds'] = criteria
        local_infile_dict['name'] = name
        local_infile_dict['machine'] = machine

        manager_io.write_input(local_infile_dict)

        # Make an empty .out file to prevent the resubmission module from mistakenly submitting this job twice
        f = open(name + '.out', 'w')
        f.close()

        os.chdir(home)

        return [os.path.join(PATH, name + '_jobscript')]

    else:  # This has been run before, further tighten the convergence criteria
        os.chdir(PATH)
        infile_dict = manager_io.read_infile(os.path.join(PATH, name + '.out'))
        criteria = [str(float(i) / 2.) for i in infile_dict['convergence_thresholds']]

        local_infile_dict = copy.copy(infile_dict)
        local_infile_dict['guess'] = True
        local_infile_dict['convergence_thresholds'] = criteria
        local_infile_dict['name'] = name
        local_infile_dict['machine'] = machine
        manager_io.write_input(local_infile_dict)

        extract_optimized_geo(os.path.join(PATH, 'scr', 'optim.xyz'))
        shutil.copy(os.path.join(PATH, 'scr', 'optimized.xyz'), os.path.join(PATH, name + '.xyz'))

        os.chdir(home)

        return [os.path.join(PATH, name + '_jobscript')]


def prep_hfx_resample(path, hfx_values=[0, 5, 10, 15, 20, 25, 30]):
    # Given a path to the outfile of a finished run, this preps the files for hfx resampling
    # Uses the wavefunction from the gas phase calculation as an initial guess
    # Returns a list of the PATH(s) to the jobscript(s) to start the resampling calculations(s)
    home = os.getcwd()
    path = convert_to_absolute_path(path)
    base = os.path.split(path)[0]

    results = manager_io.read_outfile(path)
    if not results['finished']:
        raise Exception('This calculation does not appear to be complete! Aborting...')

    # Check the state of the calculation and ensure than hfx resampling is valid
    infile_dict = manager_io.read_infile(path)
    if infile_dict['method'] != 'b3lyp':
        raise Exception('HFX resampling may not behave well for methods other than b3lyp!')
    if not infile_dict['hfx']:
        infile_dict['hfx'] = 20
    if infile_dict['hfx'] not in hfx_values:
        raise Exception('HFX resampling list does not contain the original hfx value!')

    # Now, start generating the base directory to hold all the hfx resampling values
    name = results['name'] + '_HFXresampling'
    hfx_path = os.path.join(base, name)
    if not os.path.isdir(hfx_path):
        os.mkdir(hfx_path)
    os.chdir(hfx_path)

    # Make the directory for the original calculation
    subname = name + '_' + str(infile_dict['hfx'])
    PATH = os.path.join(hfx_path, subname)
    if not os.path.isdir(PATH):
        os.mkdir(PATH)
    os.chdir(PATH)

    if not os.path.exists(os.path.join(PATH, subname + '.out')):
        shutil.copyfile(path, subname + '.out')
        shutil.copyfile(path.rsplit('.', 1)[0] + '_jobscript', subname + '_jobscript')
        shutil.copyfile(path.rsplit('.', 1)[0] + '.in', subname + '.in')
        shutil.copytree(os.path.join(os.path.split(path)[0], 'scr'), 'scr')
        if os.path.exists(path.rsplit('.', 1)[0] + '.xyz'):
            shutil.copyfile(path.rsplit('.', 1)[0] + '.xyz', subname + '.xyz')

    # Find the hfx resampling values that we're ready to generate
    hfx_values_to_generate = []
    existing_resampled_values = glob.glob(os.path.join(hfx_path, name + '_*'))
    for existing in existing_resampled_values:
        hfx = int(existing.rsplit('_', 1)[1])
        subname = name + '_' + str(hfx)
        outfile_path = os.path.join(existing, subname + '.out')
        if os.path.exists(outfile_path):
            if manager_io.read_outfile(outfile_path)['finished']:
                hfx_values_to_generate.append(hfx - 5)
                hfx_values_to_generate.append(hfx + 5)

    hfx_values_to_generate = list(set(hfx_values_to_generate))
    hfx_values_to_generate = [i for i in hfx_values_to_generate if i in hfx_values]

    # Now generate the additional hfx resampling values
    jobscripts = []
    for hfx in hfx_values_to_generate:
        subname = name + '_' + str(hfx)
        if os.path.exists(os.path.join(hfx_path, subname)):  # skip over values that we've already done
            continue

        os.mkdir(os.path.join(hfx_path, subname))
        os.chdir(os.path.join(hfx_path, subname))

        higher_hfx = subname.rsplit('_', 1)[0] + '_' + str(int(subname.rsplit('_', 1)[1]) + 5)
        lower_hfx = subname.rsplit('_', 1)[0] + '_' + str(int(subname.rsplit('_', 1)[1]) - 5)
        if os.path.exists(os.path.join(hfx_path, higher_hfx)):
            source_dir = os.path.join(hfx_path, higher_hfx)
        else:
            source_dir = os.path.join(hfx_path, lower_hfx)

        if infile_dict['run_type'] == 'minimize':
            optimxyz = os.path.join(source_dir, 'scr', 'optim.xyz')
        else:
            optimxyz = os.path.join(source_dir, 'scr', 'xyz.xyz')
        extract_optimized_geo(optimxyz)

        shutil.copy(os.path.join(source_dir, 'scr', 'optimized.xyz'), subname + '.xyz')
        if infile_dict['spinmult'] == 1:
            shutil.copy(os.path.join(source_dir, 'scr', 'c0'), 'c0')
            manager_io.write_jobscript(subname, custom_line='# -fin c0', machine=machine)
        elif infile_dict['spinmult'] != 1:
            shutil.copyfile(os.path.join(source_dir, 'scr', 'ca0'), os.path.join('ca0'))
            shutil.copyfile(os.path.join(source_dir, 'scr', 'cb0'), os.path.join('cb0'))
            manager_io.write_jobscript(subname, custom_line=['# -fin ca0\n', '# -fin cb0\n'], 
                                       machine=machine)

        local_infile_dict = copy.copy(infile_dict)
        local_infile_dict['guess'] = True
        local_infile_dict['hfx'] = hfx / 100.
        local_infile_dict['name'] = subname
        local_infile_dict['machine'] = machine
        manager_io.write_input(local_infile_dict)
        jobscripts.append(os.path.join(os.getcwd(), subname + '_jobscript'))

    os.chdir(home)

    return jobscripts
