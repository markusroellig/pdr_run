"""Parameter management for PDR models."""

import math
import random
import logging
import itertools
import numpy as np

from pdr_run.config.default_config import DEFAULT_PARAMETERS, PDR_CONFIG

logger = logging.getLogger('dev')

def compute_mass(rtot, density, alpha, rcore):
    """Compute the cloud mass in solar mass from radius (cm) and density.
    
    Args:
        rtot (float): Total radius (cm)
        density (float): Density
        alpha (float): Alpha parameter
        rcore (float): Core radius
        
    Returns:
        float: Cloud mass in solar mass
    """
    return 8.41629e-58 * (
        ((12 * density * math.pi * rtot**3) / (9 - 3 * alpha)) - 
        ((4 * density * math.pi * rtot**3 * rcore**(3 - alpha) * alpha) / (9 - 3 * alpha))
    )

def compute_radius(cmass, density, alpha, rcore):
    """Compute the cloud radius (cm) from mass (solar mass) and density.
    
    Args:
        cmass (float): Cloud mass in solar mass
        density (float): Density
        alpha (float): Alpha parameter
        rcore (float): Core radius
        
    Returns:
        float: Cloud radius in cm
    """
    return 1.05916e19 * (cmass / (
        (12 * density * math.pi) / (9 - 3 * alpha) - 
        (4 * density * math.pi * rcore**(3 - alpha) * alpha) / (9 - 3 * alpha)
    ))**(1./3.)

def from_string_to_par_log(strg):
    """Convert string parameter to numeric value (log scale).
    
    Args:
        strg (str): String parameter
        
    Returns:
        float: Numeric value
    """
    return eval(strg)

def from_string_to_par(strg):
    """Convert string parameter to numeric value.
    
    Args:
        strg (str): String parameter
        
    Returns:
        float: Numeric value
    """
    return 10**(0.1 * (eval(strg)))

def from_par_to_string(par):
    """Convert numeric value to string parameter.
    
    Args:
        par (float): Numeric value
        
    Returns:
        str: String parameter
    """
    if par > 0:
        strg = str(round(10. * math.log10(par)))
    else:
        strg = "-99"
    
    if strg == "0":
        return "00"
    else:
        return strg

def from_par_to_string_log(par):
    """Convert numeric value to string parameter (log scale).
    
    Args:
        par (float): Numeric value
        
    Returns:
        str: String parameter
    """
    strg = str(par)
    if par < 0 and par > -10:
        strg = strg.replace("-", "-0")
    if par > 0 and par < 10:
        strg = "0" + strg
    if strg == "0":
        return "00"
    else:
        return strg

def string_to_list(strg):
    """Split string on whitespace to create a list.
    
    Args:
        strg (str): String to split
        
    Returns:
        list: List of elements
    """
    return strg.split()

def list_to_string(lst):
    """Join list elements with whitespace.
    
    Args:
        lst (list): List to join
        
    Returns:
        str: Joined string
    """
    return " ".join(lst)

def random_parameter_list(par_range, num, seed=None):
    """Generate a list of random parameters within a given range.
    
    Args:
        par_range (list): Parameter range as list of strings
        num (int): Number of parameters to generate
        seed (int, optional): Random seed. Defaults to None.
        
    Returns:
        list: List of random parameters
    """
    if seed is not None:
        random.seed(seed)
    
    if len(par_range) == 1:
        return par_range * num
    
    nums = list(map(from_string_to_par_log, par_range))
    lownum = min(nums)
    upnum = max(nums)
    
    randomlist = []
    for i in range(num):
        n = random.randint(lownum, upnum)
        randomlist.append(from_par_to_string_log(n))
    
    return randomlist

def generate_parameter_combinations(config=None):
    """Generate all parameter combinations for model runs.
    
    Args:
        config (dict, optional): Parameter configuration. Defaults to None.
        
    Returns:
        list: List of parameter combinations
    """
    if config is None:
        config = DEFAULT_PARAMETERS
    
    if config.get('create_random_models', False):
        # Generate random parameter combinations
        num = config.get('random_model_num', 50)
        metal_list = random_parameter_list(config['metal'], num)
        dens_list = random_parameter_list(config['dens'], num)
        mass_list = random_parameter_list(config['mass'], num)
        chi_list = random_parameter_list(config['chi'], num)
        col_list = random_parameter_list(config['col'], num)
        
        combinations = np.array([
            metal_list, dens_list, mass_list, chi_list, col_list
        ]).transpose()
        
        logger.info(f"{num} random parameter combinations created")
        return combinations.tolist()
    else:
        # Generate all combinations
        combinations = list(itertools.product(
            config['metal'],
            config['dens'],
            config['mass'],
            config['chi'],
            config['col']
        ))
        
        logger.info(f"{len(combinations)} parameter combinations created")
        return combinations