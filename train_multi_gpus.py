# -*- coding: utf-8 -*-
#/usr/bin/python2
'''
By kyubyong park. kbpark.linguist@gmail.com. 
https://www.github.com/kyubyong/tacotron
'''

from __future__ import print_function
import tensorflow as tf
import numpy as np
import librosa

import os
from tqdm import tqdm

from hyperparams import Hyperparams as hp
from prepro import *
from networks import encode, decode1, decode2
from modules import *
from data_load import get_batch
from utils import shift_by_one
                     
class Graph:
    def __init__(self, is_training=True):
        self.graph = tf.Graph()
        
        with self.graph.as_default():
            if is_training:
                self.x, self.y, self.z, self.num_batch = get_batch()
                self.decoder_inputs = shift_by_one(self.y)
                
                # Note that batch size was multiplied by # gpus.
                # Now we split the mini-batch data by # gpus.
                self.x = tf.split(self.x, hp.num_gpus, 0)
                self.y = tf.split(self.y, hp.num_gpus, 0)
                self.z = tf.split(self.z, hp.num_gpus, 0)
                self.decoder_inputs = tf.split(self.decoder_inputs, hp.num_gpus, 0)
                
                # optimizer
                self.optimizer = tf.train.AdamOptimizer(learning_rate=hp.lr)
            
                self.losses, self.grads_and_vars_list = [], []
                for i in range(hp.num_gpus):
                    with tf.variable_scope('net', reuse=bool(i)):
                        with tf.device('/gpu:{}'.format(i)):
                            with tf.name_scope('gpu_{}'.format(i)):
                                # Encoder
                                self.memory = encode(self.x[i], is_training=is_training) # (N, T, hp.n_mels*hp.r)
                                
                                # Decoder
                                self.outputs = decode(self.decoder_inputs, self.memory, is_training=is_training) # (N, T', V)
                                 
                                # Loss
                                self.istarget = tf.to_float(tf.not_equal(self.y, 0))
                                self.mean_loss = tf.reduce_sum(self.loss*self.istarget) / (tf.reduce_sum(self.istarget) + 1e-7)
                                
                                self.losses.append(self.mean_loss)
                                self.grads_and_vars = self.optimizer.compute_gradients(self.mean_loss) 
                                self.grads_and_vars_list.append(self.grads_and_vars)    
                
                with tf.device('/cpu:0'):
                    # Aggregate losses, then calculate average loss.
                    self.loss = tf.add_n(self.losses) / len(self.losses)
                     
                    #Aggregate gradients, then calculate average gradients.
                    self.mean_grads_and_vars = []
                    for grads_and_vars in zip(*self.grads_and_vars_list):
                        grads = []
                        for grad, var in grads_and_vars:
                            grads.append(tf.expand_dims(grad, 0))
                        mean_grad = tf.reduce_mean(tf.concat(grads, 0), 0) #()
                        self.mean_grads_and_vars.append((mean_grad, var))
                 
                # Training Scheme
                self.global_step = tf.Variable(0, name='global_step', trainable=False)
                self.train_op = self.optimizer.apply_gradients(self.mean_grads_and_vars, self.global_step)
                 
                # Summmary 
                tf.summary.scalar('loss', self.loss)
                self.merged = tf.summary.merge_all()
                
            else: # Evaluation
                self.x = tf.placeholder(tf.int32, shape=(None, None))
                self.decoder_inputs = tf.placeholder(tf.float32, shape=(None, None, hp.n_mels*hp.r))
                
                with tf.variable_scope('net'):
                    # Encoder
                    self.memory = encode(self.x[i], is_training=is_training) # (N, T, hp.n_mels*hp.r)
                    
                    # Decoder
                    self.outputs = decode(self.decoder_inputs, self.memory, is_training=is_training) # (N, T', V)
                                
         
def main():   
    g = Graph(); print("Training Graph loaded")
    
    with g.graph.as_default():
        # Load vocabulary 
        char2idx, idx2char = load_vocab()
         
        # Training 
        sv = tf.train.Supervisor(logdir=hp.logdir,
                                 save_model_secs=0)
        with sv.managed_session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
            for epoch in range(1, hp.num_epochs+1): 
                if sv.should_stop(): break
                for step in tqdm(range(g.num_batch), total=g.num_batch, ncols=70, leave=False, unit='b'):
                    sess.run(g.train_op)
                 
                # Write checkpoint files at every epoch
                gs = sess.run(g.global_step) 
                sv.saver.save(sess, hp.logdir + '/model_epoch_%02d_gs_%d' % (epoch, gs))

if __name__ == '__main__':
    main()
    print("Done")
            